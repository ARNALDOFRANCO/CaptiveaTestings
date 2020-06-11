# -*- coding: utf-8 -*-
# Part of Odoo. See COPYRIGHT & LICENSE files for full copyright and licensing details.

import time
import random

from odoo import api, fields, models


class ResPartner(models.Model):
    _inherit = "res.partner"

    square_customer_id = fields.Char(string="Square Customer ID", readonly=True, copy=False)

    def _get_customer_id(self, country):
        return "".join([country, time.strftime('%y%m%d'), str(random.randint(0, 10000)).zfill(5)]).strip()

    def get_partner_billing_address(self):
        self.ensure_one()
        billing = {}
        cus_type = 'individual'
        partner_invoice_id = self
        if self.is_company:
            cus_type = 'business'
            addr = self.address_get(['invoice'])
            partner_invoice_id = addr['invoice']
            partner_invoice_id = self.browse(partner_invoice_id)
        billing.update({
            'address_line_1': partner_invoice_id.street,
            'address_line_2': partner_invoice_id.street2,
            'locality': partner_invoice_id.city,
            'postal_code': partner_invoice_id.zip,
            'country': partner_invoice_id.country_id.code,
        })
        return {
            'customer_type': cus_type,
            'billing': billing,
        }
