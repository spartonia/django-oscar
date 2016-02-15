# -*- coding: utf-8 -*-
import logging

from django import http
from django.contrib import messages
from django.contrib.auth import login
from django.core.urlresolvers import reverse, reverse_lazy
from django.shortcuts import redirect
from django.utils import six
from django.utils.http import urlquote
from django.utils.translation import ugettext as _
from django.views import generic

from oscar.apps.shipping.methods import NoShippingRequired
from oscar.core.loading import get_class, get_classes, get_model

from oscar.apps.checkout import signals

from .forms import form_selector, HomeCleaningServiceForm, ServiceLocationAddress, StripeTokenForm


ShippingAddressForm, ShippingMethodForm, GatewayForm \
    = get_classes('checkout.forms', ['ShippingAddressForm', 'ShippingMethodForm', 'GatewayForm'])
OrderCreator = get_class('order.utils', 'OrderCreator')
UserAddressForm = get_class('address.forms', 'UserAddressForm')
Repository = get_class('shipping.repository', 'Repository')
AccountAuthView = get_class('customer.views', 'AccountAuthView')
RedirectRequired, UnableToTakePayment, PaymentError \
    = get_classes('payment.exceptions', ['RedirectRequired',
                                         'UnableToTakePayment',
                                         'PaymentError'])
UnableToPlaceOrder = get_class('order.exceptions', 'UnableToPlaceOrder')
OrderPlacementMixin = get_class('checkout.mixins', 'OrderPlacementMixin')
CheckoutSessionMixin = get_class('checkout.session', 'CheckoutSessionMixin')
Order = get_model('order', 'Order')
ShippingAddress = get_model('order', 'ShippingAddress')
CommunicationEvent = get_model('order', 'CommunicationEvent')
PaymentEventType = get_model('order', 'PaymentEventType')
PaymentEvent = get_model('order', 'PaymentEvent')
UserAddress = get_model('address', 'UserAddress')
Basket = get_model('basket', 'Basket')
Email = get_model('customer', 'Email')
Country = get_model('address', 'Country')
CommunicationEventType = get_model('customer', 'CommunicationEventType')

CorePaymentDetailsView = get_class('checkout.views', 'PaymentDetailsView')

from django.conf import settings
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt

from .facade import Facade
from . import PAYMENT_METHOD_STRIPE, PAYMENT_EVENT_PURCHASE, STRIPE_EMAIL, STRIPE_TOKEN


# Standard logger for checkout events
logger = logging.getLogger('oscar.checkout')


# ======================
# Service Details (cart)
# ======================
class DetailsView(CheckoutSessionMixin, generic.FormView):
    """
    First page of the checkout, collects the service specification. This view basically is
    (should be) cart application. The view and its forms vary according to the service choice,
     eg Homestadning, flyttstadning, etc and their related extras.
    """

    def get_service_form(self):
        return form_selector(self.request)

    template_name = 'checkout/gateway.html'
    form_class = HomeCleaningServiceForm  # get_service_form()
    success_url = reverse_lazy('checkout:address')

    def get(self, request, *args, **kwargs):
        return super(DetailsView, self).get(request, *args, **kwargs)

    def form_valid(self, form):
        # We raise a signal to indicate that the user has entered the
        # checkout process.
        signals.start_checkout.send_robust(
            sender=self, request=self.request
        )

        return redirect(self.get_success_url())

    def get_success_response(self):
        return redirect(self.get_success_url())


# TODO remove
class IndexView(CheckoutSessionMixin, generic.FormView):
    """
    First page of the checkout.  We prompt user to either sign in, or
    to proceed as a guest (where we still collect their email address).
    """
    template_name = 'checkout/gateway.html'
    form_class = GatewayForm
    success_url = reverse_lazy('checkout:address')
    pre_conditions = [
        'check_basket_is_not_empty',
        'check_basket_is_valid']

    def get(self, request, *args, **kwargs):
        # We redirect immediately to shipping address stage if the user is
        # signed in.
        if request.user.is_authenticated():
            # We raise a signal to indicate that the user has entered the
            # checkout process so analytics tools can track this event.
            signals.start_checkout.send_robust(
                sender=self, request=request)
            return self.get_success_response()
        return super(IndexView, self).get(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super(IndexView, self).get_form_kwargs()
        email = self.checkout_session.get_guest_email()
        if email:
            kwargs['initial'] = {
                'username': email,
            }
        return kwargs

    def form_valid(self, form):
        if form.is_guest_checkout() or form.is_new_account_checkout():
            email = form.cleaned_data['username']
            self.checkout_session.set_guest_email(email)

            # We raise a signal to indicate that the user has entered the
            # checkout process by specifying an email address.
            signals.start_checkout.send_robust(
                sender=self, request=self.request, email=email)

            if form.is_new_account_checkout():
                messages.info(
                    self.request,
                    _("Create your account and then you will be redirected "
                      "back to the checkout process"))
                self.success_url = "%s?next=%s&email=%s" % (
                    reverse('customer:register'),
                    reverse('checkout:address'),
                    urlquote(email)
                )
        else:
            user = form.get_user()
            login(self.request, user)

            # We raise a signal to indicate that the user has entered the
            # checkout process.
            signals.start_checkout.send_robust(
                sender=self, request=self.request)

        return redirect(self.get_success_url())

    def get_success_response(self):
        return redirect(self.get_success_url())


# ================
# ADDRESS
# ================

class AddressView(CheckoutSessionMixin, generic.FormView):
    """
    Determine the address for the order.

    user handling logic:
    if user.authenticated():
        form_initial = get_address()
    else:
        login_form() : opt for login,
        if logged_in:
            form_initial = get_address()
        address_form()

    TODO: required?
    The default behaviour is to display a list of addresses from the users's
    address book, from which the user can choose one to be their shipping
    address.  They can add/edit/delete these USER addresses.  This address will
    be automatically converted into a SHIPPING address when the user checks
    out.
    # TODO
    s = ['check_basket_is_not_empty',
    #                   'check_basket_is_valid',
    #                   'check_user_email_is_captured']
    # skip_conditions = ['skip_unless_basket_requires_shipping']


    Alternatively, the user can enter a SHIPPING address directly which will be
    saved in the session and later saved as ShippingAddress model when the
    order is successfully submitted.
    """
    template_name = 'checkout/address.html'
    form_class = ServiceLocationAddress
    success_url = reverse_lazy('checkout:preview')

    # TODO
    # pre_conditions = ['check_basket_is_not_empty',
    #                   'check_basket_is_valid',
    #                   'check_user_email_is_captured']
    # skip_conditions = ['skip_unless_basket_requires_shipping']

    def get_context_data(self, **kwargs):
        ctx = super(AddressView, self).get_context_data(**kwargs)
        if self.request.user.is_authenticated():
            # Look up address book data
            ctx['addresses'] = self.get_available_addresses()
        return ctx

    def get_available_addresses(self):
        # Include only addresses where the country is flagged as valid for
        # shipping. Also, use ordering to ensure the default address comes
        # first.
        return self.request.user.addresses.all().order_by(
            '-is_default_for_shipping')[:2]

    # def get_initial(self):
    #     if self.request.user.is_authenticated() and self.get_available_addresses().exists():
    #         initial = self.get_available_addresses()[0]
    #         return initial

    def post(self, request, *args, **kwargs):
        # Check if a shipping address was selected directly (eg no form was
        # filled in)
        if self.request.user.is_authenticated() \
                and 'address_id' in self.request.POST:
            address = UserAddress._default_manager.get(
                pk=self.request.POST['address_id'], user=self.request.user)
            action = self.request.POST.get('action', None)
            if action == 'ship_to':
                # User has selected a previous address to ship to
                self.checkout_session.ship_to_user_address(address)
                return redirect(self.get_success_url())
            else:
                return http.HttpResponseBadRequest()
        else:
            return super(AddressView, self).post(
                request, *args, **kwargs)

    def form_valid(self, form):
        # Store the address details in the session and redirect to next step
        address_fields = dict(
            (k, v) for (k, v) in form.instance.__dict__.items()
            if not k.startswith('_'))
        self.checkout_session.ship_to_new_address(address_fields)
        return super(AddressView, self).form_valid(form)


class UserAddressUpdateView(CheckoutSessionMixin, generic.UpdateView):
    """
    Update a user address
    """
    template_name = 'checkout/user_address_form.html'
    form_class = UserAddressForm
    success_url = reverse_lazy('checkout:address')

    def get_queryset(self):
        return self.request.user.addresses.all()

    def get_form_kwargs(self):
        kwargs = super(UserAddressUpdateView, self).get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

    def get_success_url(self):
        messages.info(self.request, _("Address saved"))
        return super(UserAddressUpdateView, self).get_success_url()


class UserAddressDeleteView(CheckoutSessionMixin, generic.DeleteView):
    """
    Delete an address from a user's addressbook.
    """
    template_name = 'checkout/user_address_delete.html'
    success_url = reverse_lazy('checkout:address')

    def get_queryset(self):
        return self.request.user.addresses.all()

    def get_success_url(self):
        messages.info(self.request, _("Address deleted"))
        return super(UserAddressDeleteView, self).get_success_url()


# ===============
# Shipping method
# ===============

# TODO remove
class ShippingMethodView(CheckoutSessionMixin, generic.FormView):
    """
    View for allowing a user to choose a shipping method.

    Shipping methods are largely domain-specific and so this view
    will commonly need to be subclassed and customised.

    The default behaviour is to load all the available shipping methods
    using the shipping Repository.  If there is only 1, then it is
    automatically selected.  Otherwise, a page is rendered where
    the user can choose the appropriate one.
    """
    template_name = 'checkout/shipping_methods.html'
    form_class = ShippingMethodForm
    pre_conditions = ['check_basket_is_not_empty',
                      'check_basket_is_valid',
                      'check_user_email_is_captured']

    def post(self, request, *args, **kwargs):
        self._methods = self.get_available_shipping_methods()
        return super(ShippingMethodView, self).post(request, *args, **kwargs)

    def get(self, request, *args, **kwargs):
        # These pre-conditions can't easily be factored out into the normal
        # pre-conditions as they do more than run a test and then raise an
        # exception on failure.

        # Check that shipping is required at all
        if not request.basket.is_shipping_required():
            # No shipping required - we store a special code to indicate so.
            self.checkout_session.use_shipping_method(
                NoShippingRequired().code)
            return self.get_success_response()

        # Check that shipping address has been completed
        if not self.checkout_session.is_shipping_address_set():
            messages.error(request, _("Please choose a shipping address"))
            return redirect('checkout:shipping-address')

        # Save shipping methods as instance var as we need them both here
        # and when setting the context vars.
        self._methods = self.get_available_shipping_methods()
        if len(self._methods) == 0:
            # No shipping methods available for given address
            messages.warning(request, _(
                "Shipping is unavailable for your chosen address - please "
                "choose another"))
            return redirect('checkout:shipping-address')
        elif len(self._methods) == 1:
            # Only one shipping method - set this and redirect onto the next
            # step
            self.checkout_session.use_shipping_method(self._methods[0].code)
            return self.get_success_response()

        # Must be more than one available shipping method, we present them to
        # the user to make a choice.
        return super(ShippingMethodView, self).get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        kwargs = super(ShippingMethodView, self).get_context_data(**kwargs)
        kwargs['methods'] = self._methods
        return kwargs

    def get_form_kwargs(self):
        kwargs = super(ShippingMethodView, self).get_form_kwargs()
        kwargs['methods'] = self._methods
        return kwargs

    def get_available_shipping_methods(self):
        """
        Returns all applicable shipping method objects for a given basket.
        """
        # Shipping methods can depend on the user, the contents of the basket
        # and the shipping address (so we pass all these things to the
        # repository).  I haven't come across a scenario that doesn't fit this
        # system.
        return Repository().get_shipping_methods(
            basket=self.request.basket, user=self.request.user,
            shipping_addr=self.get_shipping_address(self.request.basket),
            request=self.request)

    def form_valid(self, form):
        # Save the code for the chosen shipping method in the session
        # and continue to the next step.
        self.checkout_session.use_shipping_method(form.cleaned_data['method_code'])
        return self.get_success_response()

    def form_invalid(self, form):
        messages.error(self.request, _("Your submitted shipping method is not"
                                       " permitted"))
        return super(ShippingMethodView, self).form_invalid(form)

    def get_success_response(self):
        return redirect('checkout:payment-method')


# ==============
# Payment method
# ==============

# TODO remove?
class PaymentMethodView(CheckoutSessionMixin, generic.TemplateView):
    """
    View for a user to choose which payment method(s) they want to use.

    This would include setting allocations if payment is to be split
    between multiple sources. It's not the place for entering sensitive details
    like bankcard numbers though - that belongs on the payment details view.
    """
    pre_conditions = [
        'check_basket_is_not_empty',
        'check_basket_is_valid',
        # 'check_user_email_is_captured',
        # 'check_shipping_data_is_captured',
    ]
    skip_conditions = ['skip_unless_payment_is_required']

    def get(self, request, *args, **kwargs):
        # By default we redirect straight onto the payment details view. Shops
        # that require a choice of payment method may want to override this
        # method to implement their specific logic.
        return self.get_success_response()

    def get_success_response(self):
        return redirect('checkout:payment-details')


# ================
# Order submission
# ================

SourceType = get_model('payment', 'SourceType')
Source = get_model('payment', 'Source')


class PaymentDetailsView(CorePaymentDetailsView):

    template_name = 'checkout/payment_details_2.html'
    template_name_preview = 'checkout/preview_2.html'

    @method_decorator(csrf_exempt)
    def dispatch(self, request, *args, **kwargs):
        return super(PaymentDetailsView, self).dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        ctx = super(PaymentDetailsView, self).get_context_data(**kwargs)
        if self.preview:
            ctx['stripe_token_form'] = StripeTokenForm(self.request.POST)
            ctx['order_total_incl_tax_cents'] = (ctx['order_total'].incl_tax * 100).to_integral_value()
        else:
            ctx['stripe_publishable_key'] = settings.STRIPE_PUBLISHABLE_KEY
        return ctx

    def handle_payment(self, order_number, total, **kwargs):
        stripe_ref = Facade().charge(
            order_number,
            total,
            card=self.request.POST[STRIPE_TOKEN],
            description=self.payment_description(order_number, total, **kwargs),
            metadata=self.payment_metadata(order_number, total, **kwargs)
        )

        source_type, __ = SourceType.objects.get_or_create(name=PAYMENT_METHOD_STRIPE)
        source = Source(source_type=source_type, currency=settings.STRIPE_CURRENCY, amount_allocated=total.incl_tax,
                        amount_debited=total.incl_tax, reference=stripe_ref)
        self.add_payment_source(source)

        self.add_payment_event(PAYMENT_EVENT_PURCHASE, total.incl_tax)

    def payment_description(self, order_number, total, **kwargs):
        return self.request.POST[STRIPE_EMAIL]


    def payment_metadata(self, order_number, total, **kwargs):
        return {'order_number': order_number}



# =========
# Thank you
# =========


class ThankYouView(generic.DetailView):
    """
    Displays the 'thank you' page which summarises the order just submitted.
    """
    template_name = 'checkout/thank_you.html'
    context_object_name = 'order'

    def get_object(self):
        # We allow superusers to force an order thank-you page for testing
        order = None
        if self.request.user.is_superuser:
            if 'order_number' in self.request.GET:
                order = Order._default_manager.get(
                    number=self.request.GET['order_number'])
            elif 'order_id' in self.request.GET:
                order = Order._default_manager.get(
                    id=self.request.GET['order_id'])

        if not order:
            if 'checkout_order_id' in self.request.session:
                order = Order._default_manager.get(
                    pk=self.request.session['checkout_order_id'])
            else:
                raise http.Http404(_("No order found"))

        return order
