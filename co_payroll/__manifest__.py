# -*- coding: utf-8 -*-
# Copyright (C) 2019 Odoo Inc
{
    "name": "Colombian Payroll",
    "version": "0.1",
    "category": "",
    "description": """
    """,
    "depends": [
        "hr",
        "hr_payroll",
        "hr_payroll_account",
        "hr_contract",
        "hr_holidays",
        "l10n_co_edi",
        "account_batch_payment",
        "base_automation",
        "base_address_city",
    ],
    "data": [
        "data/hr.xml",
        "views/hr_payroll.xml",
        "views/account_batch_payment_views.xml",
        "report/report_payslip.xml",
        "views/autoliquidaciones.xml",
        "views/res_city_views.xml",
    ],
    "demo": [],
    "installable": True,
    "auto_install": False,
}
