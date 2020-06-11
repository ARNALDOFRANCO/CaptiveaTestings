# -*- coding: utf-8 -*-
# Part of Odoo. See COPYRIGHT & LICENSE files for full copyright and licensing details.

import time
import werkzeug

from uuid import uuid4

from odoo import http, _
from odoo.http import request


class SquareCheckoutController(http.Controller):
    _return_url = '/payment/square/validate'

    @http.route(['/payment/square/redirect_checkout'], type='http', auth='public', csrf=False, website=True)
    def square_feedback(self, **post):
        checkout_page_url = '/'
        acquirer = False
        tx_id = False
        if post.get('acquirer_id'):
            acquirer = request.env['payment.acquirer'].browse(int(post['acquirer_id']))
        if post.get('reference'):
            tx_id = request.env['payment.transaction'].sudo().search([('reference', '=', post['reference'])], limit=1)
        if acquirer and tx_id:
            base_url = acquirer.get_base_url()
            client = acquirer.square_client()
            model_id = tx_id._get_model_id()
            square_order = tx_id._create_order_id(ischeckout=True)
            if client and square_order:
                checkout_req = client.checkout.create_checkout(
                    location_id = post.get('location_id'),
                    body = {
                        'idempotency_key': ('CHECKOUT-%s-%s-%s' % (str(tx_id.id) ,(uuid4().hex[:25]), uuid4().hex[:25]))[:70],
                        'order': square_order,
                        'ask_for_shipping_address': True,
                        'merchant_support_email': post.get('partner_email') or '',
                        'pre_populate_buyer_email': post.get('email') or '',
                        'pre_populate_shipping_address': {
                            'address_line_1': post.get('address1') or '',
                            'address_line_2': post.get('address2') or '',
                            'locality': post.get('city') or '',
                            'administrative_district_level_1': post.get('state') or '',
                            'postal_code': post.get('zip_code') or '',
                            'country': post.get('country_code') or '',
                            'first_name': post.get('first_name') or '',
                            'last_name': post.get('last_name') or ''
                        },
                        'redirect_url': post.get('redirect_url')
                    }
                )
                if checkout_req.is_success():
                    checkout_page_url = checkout_req.body.get('checkout').get('checkout_page_url')
                elif checkout_req.is_error():
                    errors = checkout_req.errors[0]
                    return request.render('sync_payment_square.square_template', {'error_msg': errors['code'] + ' : ' + errors['detail']})
            else:
                return request.render('sync_payment_square.square_template', {'error_msg': 'Your Credentials for Square Payment Gateway is not Valid.Please Verify.'})
        return werkzeug.utils.redirect(checkout_page_url)

    @http.route(['/payment/square/validate'], type='http', auth='public',csrf=False, website=True)
    def payment_square_validate(self, checkoutId, referenceId, transactionId):
        post_data = {
            'checkoutId': checkoutId,
            'referenceId': referenceId,
            'transactionId': transactionId
        }
        time.sleep(5)
        transaction_id = request.env['payment.transaction'].sudo().search([('reference', '=', referenceId)], limit=1)
        if transaction_id:
            client = transaction_id.acquirer_id.square_client()
            if client:
                get_transaction = client.transactions.retrieve_transaction(location_id=transaction_id.acquirer_id.square_location_id, transaction_id=transactionId)
                if get_transaction.is_success():
                    post_data.update(get_transaction.body.get('transaction'))
                elif get_transaction.is_error():
                    errors = get_transaction.errors[0]
                    post_data.update(errors)
            request.env['payment.transaction'].sudo().form_feedback(post_data, 'square')
        return werkzeug.utils.redirect('/payment/process')

    @http.route(['/payment/square/s2s/create_json_3ds'], type='json', auth='public', csrf=False)
    def square_s2s_create_json_3ds(self, verify_validity=False, **kwargs):
        token = False
        if not kwargs.get('partner_id'):
            kwargs = dict(kwargs, partner_id=request.env.user.partner_id.id)
        acquirer_id = request.env['payment.acquirer'].browse(int(kwargs.get('acquirer_id')))
        if acquirer_id:
            token = acquirer_id.s2s_process(kwargs)
        if not token:
            return {'result': False}
        res = {
            'result': True,
            'id': token.id,
            'short_name': token.short_name,
            '3d_secure': False,
            'verified': False,
        }

        if verify_validity != False:
            token.verified = True
            res['verified'] = token.verified
        return res

    @http.route(['/payment/square/s2s/create_json'], type='json', auth='public')
    def square_s2s_create_json(self, **kwargs):
        acquirer_id = int(kwargs.get('acquirer_id'))
        acquirer = request.env['payment.acquirer'].browse(acquirer_id)
        if not kwargs.get('partner_id'):
            kwargs = dict(kwargs, partner_id=request.env.user.partner_id.id)
        return acquirer.s2s_process(kwargs).id
