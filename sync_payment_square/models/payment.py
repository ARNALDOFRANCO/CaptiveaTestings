# -*- coding: utf-8 -*-
# Part of Odoo. See COPYRIGHT & LICENSE files for full copyright and licensing details.

import logging
from uuid import uuid4
from werkzeug import urls

from odoo import api, fields, models, _
from odoo.tools.float_utils import float_compare
from odoo.addons.payment.models.payment_acquirer import ValidationError
from odoo.addons.sync_payment_square.controllers.main import SquareCheckoutController

_logger = logging.getLogger(__name__)

try:
    from square.client import Client
except ImportError:
    _logger.error('Square payment depends on the squareup python package.')
    Client = None

def _partner_split_name(partner_name):
    return [' '.join(partner_name.split()[:-1]), ' '.join(partner_name.split()[-1:])]


class AcquirerSquare(models.Model):
    _inherit = 'payment.acquirer'

    provider = fields.Selection(selection_add=[('square',  'Square')])
    square_application_id = fields.Char('Application ID', required_if_provider='square', groups='base.group_user')
    square_location_id = fields.Char('Location ID', required_if_provider='square', groups='base.group_user')
    square_access_token = fields.Char('Access Token', required_if_provider='square', groups='base.group_user')

    def _get_feature_support(self):
        """Get advanced feature support by provider.

        Each provider should add its technical in the corresponding
        key for the following features:
            * fees: support payment fees computations
            * authorize: support authorizing payment (separates
                         authorization and capture)
            * tokenize: support saving payment data in a payment.tokenize
                        object
        """
        res = super(AcquirerSquare, self)._get_feature_support()
        res['authorize'].append('square')
        res['tokenize'].append('square')
        return res

    @api.model
    def _create_missing_journal_for_acquirers(self, company=None):
        journals = super(AcquirerSquare, self)._create_missing_journal_for_acquirers(company=company)
        company = company or self.env.company
        acquirers = self.env['payment.acquirer'].search(
            [('provider', '=', 'square'), ('journal_id', '=', False), ('company_id', '=', company.id)])
        for acquirer in acquirers.filtered(lambda l: not l.journal_id and l.company_id.chart_template_id):
            acquirer.journal_id = self.env['account.journal'].create(acquirer._prepare_account_journal_vals())
            journals += acquirer.journal_id
        return journals

    def square_form_generate_values(self, values):
        self.ensure_one()
        base_url = self.get_base_url()
        values.update({
            'square_application_id': self.square_application_id,
            'square_location_id': self.square_location_id,
            'square_access_token': self.square_access_token,
            'redirect_url': urls.url_join(base_url, SquareCheckoutController._return_url)
        })
        return values

    def square_get_form_action_url(self):
        return '/payment/square/redirect_checkout'

    @api.model
    def square_s2s_form_process(self, data):
        """
        This Method is used to create a Token from credit card Information
        """
        PaymentMethod = False
        if data.get('card_data') and data.get('payment_nonce'):
            values = {
                'name': 'XXXXXXXXXXXX%s' % (data['card_data'].get('last_4')),
                'acquirer_id': int(data.get('acquirer_id')) or self.id,
                'partner_id': int(data.get('partner_id')),
                'square_card_nonce': data.get('payment_nonce')
            }
            PaymentMethod = self.env['payment.token'].sudo().create(values)
        return PaymentMethod

    def square_client(self):
        """ Method is used for create square client. """
        self.ensure_one()
        client = False
        if self.sudo().square_access_token and self.sudo().square_access_token == 'dummy':
            raise ValidationError(_("Please configure square account."))
        if self.sudo().square_access_token:
            client = Client(
                access_token=self.sudo().square_access_token,
                environment='sandbox' if self.state == 'test' else 'production',
            )
        return client


class PaymentTransactionSquare(models.Model):
    _inherit = 'payment.transaction'

    square_order_id = fields.Char('Square Order ID', readonly=True, copy=False)

    @api.model
    def _square_form_get_tx_from_data(self, data):
        reference = data.get('reference_id') or data.get('referenceId')
        if not reference:
            error_msg = 'Square: received data with missing reference (%s)' % (reference)
            _logger.error(error_msg)
            raise ValidationError(error_msg)

        tx = self.search([('reference', '=', reference)])
        if not tx or len(tx) > 1:
            error_msg = 'Square: received data for reference %s' % reference
            if not tx:
                error_msg += '; no order found'
            else:
                error_msg += '; multiple order found'
            _logger.info(error_msg)
            raise ValidationError(error_msg)
        return tx[0]

    def _square_form_get_invalid_parameters(self, data):
        invalid_parameters = []
        reference = data.get('reference_id') or data.get('referenceId')
        if reference and reference != self.reference:
            invalid_parameters.append(('Reference', reference, self.reference))

        #check what is buyed
        if data.get('tenders') and float_compare(float(data['tenders'][0]['amount_money']['amount'] / 100), self.amount, 2) != 0:
            invalid_parameters.append(('Amount', data['tenders'][0]['amount_money']['amount'], '%.2f' % self.amount))
        return invalid_parameters

    def _square_form_validate(self, tree):
        self.ensure_one()
        if self.state in ['done']:
            _logger.warning('Square: trying to validate an already validated tx (ref %s)' % self.reference)
            return True
        status = tree.get('tenders') and tree.get('tenders')[0]['card_details']['status'] or False
        if status == 'CAPTURED':
            self.write({
                'acquirer_reference': tree.get('id'),
                'square_order_id': tree.get('order_id')
            })
            self._set_transaction_done()
            self.execute_callback()
            return True
        elif status in ['VOIDED', 'FAILED']:
            self.write({
                'state_message':status,
                'acquirer_reference': tree.get('id'),
            })
            self._set_transaction_cancel()
            return False
        else:
            self.write({
                'state': 'error',
                'state_message': tree.get('code') + ' :' + tree.get('detail'),
                'acquirer_reference': tree.get('id') or '',
            })
            return False

    def square_s2s_do_transaction(self, **data):
        """ Method is used for saved card payment. """
        self.ensure_one()
        square_payment = {}
        client = self.acquirer_id.square_client()
        if client:
            order_id = self._create_order_id()
            if order_id:
                payment_vals = {
                    "idempotency_key": ('ODOO-%s-%s-%s' % (str(self.id) ,(uuid4().hex[:15]), uuid4().hex[:15]))[:35],
                    "amount_money": {
                        "amount": round(self.amount * 100, 2),
                        "currency": self.currency_id.name
                    },
                    "source_id": self.payment_token_id.acquirer_ref,
                    "autocomplete": True,
                    "customer_id": self.payment_token_id.partner_id.square_customer_id or '',
                    "location_id": self.acquirer_id.square_location_id,
                    "order_id": order_id,
                    "reference_id": self.reference,
                }
                if self.acquirer_id.capture_manually:
                    payment_vals.update({'autocomplete': False})
                square_payment_req = client.payments.create_payment(
                    body = payment_vals
                )
                if square_payment_req.is_success():
                    square_payment = square_payment_req.body.get('payment')
                elif square_payment_req.is_error():
                    square_payment = square_payment_req.errors[0]
            return self._square_s2s_validate_tree(square_payment)

    def _create_order_id(self, ischeckout=None):
        """ Method is used for create order id of payment square. """
        model_id = self._get_model_id()
        client = self.acquirer_id.square_client()
        line_items, taxes, discounts = [], [], []
        if model_id and model_id._name == 'account.move' and client:
            # total_amount = 0.0
            product_id = self.env['ir.config_parameter'].sudo().get_param('sale.default_deposit_product_id')
            for line in model_id.invoice_line_ids:
                line_discount = []
                if line.product_id.id == int(product_id) and line.quantity < 0:
                    order = self.env['sale.order'].search([('name', '=', line.origin)], limit=1)
                    discounts.append({
                        'name': line.product_id.name,
                        'percentage': str(abs(round(100.0 * line.price_subtotal / order.amount_untaxed, 6)))
                    })
                else:
                    if line.discount:
                        line_discount.append({
                            'name': ('Discount %s' % (str(line.discount))),
                            'percentage': str(abs(round(line.discount, 6)))
                        })
                    line_items.append({
                        'name': line.product_id.name,
                        'quantity': str(abs(line.quantity)),
                        'base_price_money': {
                            'amount': int(line.price_unit * 100),
                            'currency': model_id.currency_id.name
                        },
                        'discounts': line_discount
                    })
                    # total_amount += line.price_subtotal
            if model_id.amount_tax:
                taxes.append({
                    'name': 'TAX',
                    'percentage': str(round(100.0 * model_id.amount_tax / model_id.amount_untaxed, 6))
                })
        elif model_id and model_id._name == 'sale.order' and client:
            total_amount = 0.0
            for line in model_id.order_line:
                line_discount = []
                if line.discount:
                    line_discount.append({
                        'name': ('Discount %s' % (str(line.discount))),
                        'percentage': str(abs(round(line.discount, 6)))
                    })
                line_items.append({
                    'name': line.product_id.name,
                    'quantity': str(abs(line.product_uom_qty)),
                    'base_price_money': {
                        'amount': int(line.price_unit * 100),
                        'currency': model_id.currency_id.name
                    },
                    'discounts': line_discount
                })
                total_amount += line.product_uom_qty * line.price_unit
            if model_id.amount_tax:
                taxes.append({
                    'name': 'TAX',
                    'percentage': str(round(100.0 * model_id.amount_tax / model_id.amount_untaxed, 6))
                })
        order = {
            'reference_id': self.reference,
            'line_items': line_items,
            'taxes': taxes,
            'discounts': discounts,
        }
        if ischeckout:
            return order
        result = client.orders.create_order(
            location_id = self.acquirer_id.square_location_id,
            body = {
                'idempotency_key': ('ORDER-%s-%s-%s' % (self.id ,(uuid4().hex[:15]), uuid4().hex[:15]))[:35],
                'order': order
            }
        )
        if result.is_success():
            return result.body.get('order').get('id')
        elif result.is_error():
            errors = result.errors[0]
            return self._square_s2s_validate_tree(errors)

    def _square_s2s_validate_tree(self, tree):
        self.ensure_one()
        return self._square_s2s_validate(tree)

    def _get_model_id(self):
        """ Get model id of order. """
        model_id = False
        reference = self.reference.split('-')
        if 'x' in self.reference:
            reference = self.reference.split('x')
        if reference:
            model_id = self.env['account.move'].sudo().search([('name', '=', reference[0])], limit=1)
            if not model_id:
                model_id = self.env['sale.order'].sudo().search([('name', '=', reference[0])], limit=1)
        return model_id

    def _square_s2s_validate(self, tree):
        """ Method used for validate payment responce which is comming from square payment. """
        if self.state in ['done']:
            _logger.warning('Square: trying to validate an already validated tx (ref %s)' % self.reference)
            return True
        status = tree.get('status')
        if status == 'COMPLETED':
            self.write({
                'acquirer_reference': tree.get('id'),
                'square_order_id': tree.get('order_id')
            })
            self.execute_callback()
            self._set_transaction_done()
            if self.payment_token_id:
                self.payment_token_id.verified = True
            return True
        elif status == 'APPROVED' and tree['card_details']['status'] == 'AUTHORIZED':
            self.write({
                'acquirer_reference': tree.get('id'),
                'square_order_id': tree.get('order_id')
            })
            self._set_transaction_authorized()
            if self.payment_token_id:
                self.payment_token_id.verified = True
            return True
        elif status == 'PENDING':
            self.write({
                'acquirer_reference': tree.get('id'),
                'square_order_id': tree.get('order_id')
            })
            self._set_transaction_pending()
            return True
        elif status in ['CANCELED', 'REJECTED', 'FAILED']:
            self.write({
                'acquirer_reference': tree.get('id'),
            })
            self._set_transaction_cancel()
            return True
        if tree.get('code'):
            self.write({
                'state': 'error',
                'state_message': tree.get('code') + ' :' + tree.get('detail'),
                'acquirer_reference': tree.get('id') or '',
            })
            return False

    def square_s2s_capture_transaction(self):
        """
        This Method is used to Capture Transaction when it's in 'Authorize' state.
        """
        self.ensure_one()
        response = {}
        if not self.acquirer_reference:
            raise ValidationError("Transaction ID Not Found")
        else:
            client = self.acquirer_id.square_client()
            if client:
                capture_request = client.payments.complete_payment(
                    payment_id = self.acquirer_reference
                )
                if capture_request.is_success():
                    response = capture_request.body.get('payment')
                elif capture_request.is_error():
                    response = capture_request.errors[0]
            return self._square_s2s_validate_tree(response)

    def square_s2s_void_transaction(self):
        """
        This Method is used to Void(Cancel) Transaction when it's in 'Authorize' state.
        """
        self.ensure_one()
        if self.acquirer_id and self.acquirer_reference:
            client = self.acquirer_id.square_client()
            if client:
                cancel_request = client.payments.cancel_payment(
                    payment_id = self.acquirer_reference
                )
                if cancel_request.is_success():
                    response = cancel_request.body.get('payment')
                elif cancel_request.is_error():
                    response = cancel_request.errors[0]
        return self._square_s2s_validate_tree(response)


class PaymentToken(models.Model):
    _inherit = 'payment.token'

    square_card_nonce = fields.Char('Square Card Nonce', copy=False, readonly=True)

    @api.model
    def square_create(self, values):
        """ Method is used for create square token and token is link to customer. """
        if values.get('square_card_nonce') and not values.get('acquirer_ref'):
            payment_acquirer = False
            if values.get('acquirer_id'):
                payment_acquirer = self.env['payment.acquirer'].browse(values.get('acquirer_id'))
            else:
                payment_acquirer = self.env['payment.acquirer'].search([('provider', '=', 'square'), ('company_id', '=', self.env.user.company_id.id)], limit=1)
            partner_id = self.env['res.partner'].browse(values.get('partner_id'))
            try:
                if payment_acquirer:
                    client = payment_acquirer.square_client()
                    if client and partner_id and not partner_id.square_customer_id:
                        # Create square customer.
                        result = client.customers.create_customer(
                            body = {
                                "given_name": _partner_split_name(partner_id.name)[0],
                                "family_name": _partner_split_name(partner_id.name)[1],
                                "email_address": partner_id.email,
                                "address": {
                                    "address_line_1": partner_id.street or '',
                                    "address_line_2": partner_id.street2 or '',
                                    "locality": (partner_id.city) or '',
                                    "postal_code": partner_id.zip or '',
                                    "country": partner_id.country_id.code or ''
                                },
                                "phone_number": partner_id.phone,
                                "idempotency_key": ('CUST-%s-%s-%s' % (str(partner_id.id) ,(uuid4().hex[:15]), uuid4().hex[:15]))[:35],
                                "reference_id": partner_id._get_customer_id('CUST'),
                                "note": partner_id.comment or ''
                            }
                        )
                        if result.is_success():
                            partner_id.square_customer_id = result.body.get('customer').get('id')
                        elif result.is_error():
                            errors = result.errors[0]
                            raise Exception(_(errors['code'] + ' :' + errors['detail']))
                    if client and partner_id and partner_id.square_customer_id:
                        billing_details = partner_id.get_partner_billing_address()
                        billing_address = billing_details.get('billing')
                        # create square customer card.
                        cust_card = client.customers.create_customer_card(
                                customer_id = partner_id.square_customer_id,
                                body = {
                                    "card_nonce": values['square_card_nonce'],
                                    "billing_address": {
                                        "address_line_1": billing_address.get('address_line_1') or '',
                                        "address_line_2": billing_address.get('address_line_2') or '',
                                        "locality": billing_address.get('locality') or '',
                                        "postal_code": billing_address.get('postal_code') or '',
                                        "country": billing_address.get('country') or ''
                                    },
                                    "cardholder_name": partner_id.name
                                }
                            )
                        if cust_card.is_success():
                            values['acquirer_ref'] = cust_card.body.get('card').get('id')
                            return values
                        elif cust_card.is_error():
                            errors = cust_card.errors[0]
                            raise Exception(_(errors['code'] + ' :' + errors['detail']))
                else:
                    return {}
            except Exception as e:
                raise ValidationError(_("Square Error : %s !" % e))
        return {}

    def unlink(self):
        """ Method is used for delete card from square side. """
        for rec in self:
            if rec.acquirer_id and rec.acquirer_id.provider == 'square' and rec.acquirer_ref and rec.partner_id and rec.partner_id.square_customer_id:
                try:
                    client = rec.acquirer_id.square_client()
                    if client:
                        result = client.customers.delete_customer_card(
                            customer_id = rec.partner_id.square_customer_id,
                            card_id = rec.acquirer_ref
                        )
                        if result.is_success():
                            return super(PaymentToken, self).unlink()
                except Exception as e:
                    raise ValidationError(_("Square Error : %s !" % e))
        return super(PaymentToken, self).unlink()
