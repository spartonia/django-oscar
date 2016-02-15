# -*- coding: utf-8 -*-
from django import forms

from oscar.apps.address.forms import AbstractAddressForm
from oscar.views.generic import PhoneNumberMixin
from oscar.core.loading import get_model


def form_selector(request):
    """
    Returns a suitable form according the service id (which is obtained from url parameters -
     example: details?id=1).
     Parameters
     ----------
     request : <request>
    """
    service_id = 1  # TODO auto update from url params
    if service_id == 1:
        return HomeCleaningServiceForm
    elif service_id == 2:
        # return other service form
        pass


class HomeCleaningServiceForm(forms.Form):

    # TODO: fetch from database
    available_time_slots = (
        ('20hr', '02:00 Hours (~35 m2)'),
        ('25hr', '02:30 Hours (~45 m2)'),
        ('30hr', '03:00 Hours (~55 m2)'),
        ('35hr', '03:30 Hours (~65 m2)'),
        ('40hr', '04:00 Hours (~75 m2)'),
    )

    service_choice = forms.ChoiceField(
        choices=available_time_slots,
        initial=available_time_slots[2][0]
    )

    # add service extras as bool options , eg freezer, balcony, etc


class ServiceLocationAddress(AbstractAddressForm):

        class Meta:
            model = get_model('order', 'shippingaddress')
            fields = [
            'title', 'first_name', 'last_name',
            'line1', 'line2', 'line3', 'line4',
            'state', 'postcode', 'country',
            'phone_number', 'notes',
        ]


class StripeTokenForm(forms.Form):
    stripeEmail = forms.EmailField(widget=forms.HiddenInput())
    stripeToken = forms.CharField(widget=forms.HiddenInput())