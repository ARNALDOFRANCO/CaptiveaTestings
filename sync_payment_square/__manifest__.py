# -*- coding: utf-8 -*-
# Part of Odoo. See COPYRIGHT & LICENSE files for full copyright and licensing details.

{
    'name': 'Integration of Square Payment Acquirer with Odoo',
    'category': 'Accounting/Payment',
    'summary': 'Payment Acquirer: Square Implementation',
    'version': '1.0',
    'description': """Square Payment Acquirer""",
    'author': 'Synconics Technologies Pvt. Ltd.',
    'website': 'https://www.synconics.com',
    'depends': ['sale_management', 'account_payment', 'website_sale'],
    'data': [
        'views/payment_acquirer.xml',
        'views/payment_square_templates.xml',
        'views/res_partner_view.xml',
        'views/square_templete.xml',
        'views/payment_templete.xml',
        'data/payment_square_data.xml'
    ],
    'external_dependencies': {
        "python": [
            "square",
        ],
    },
    'demo': [],
    'images': [
        'static/description/main_screen.png'
    ],
    'price': 120.0,
    'currency': 'EUR',
    'license': 'OPL-1',
    'installable': True,
    'application': True,
    'auto_install': False,
    'post_init_hook': 'create_missing_journal_for_acquirers',
}
