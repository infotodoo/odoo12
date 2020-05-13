# coding: utf-8
# Copyright (C) 2019 Odoo Inc
from odoo import api, models, fields, _
from odoo.exceptions import UserError


class HrEmployee(models.Model):
    _inherit = "hr.employee"

    marital = fields.Selection(
        selection_add=[("cohabitant", "Union libre"), ("other", "Otro")]
    )


class HrPayslipWorkedDays(models.Model):
    _inherit = "hr.payslip.worked_days"

    payslip_date_from = fields.Date(
        related="payslip_id.date_from", store=True, readonly=True
    )
    payslip_state = fields.Selection(
        related="payslip_id.state", store=True, readonly=True
    )

    # make this field a stored related field instead
    contract_id = fields.Many2one(
        related="payslip_id.contract_id", store=True, readonly=False
    )


class HrPayslipInput(models.Model):
    _inherit = "hr.payslip.input"

    employee_id = fields.Many2one(related="payslip_id.employee_id", store=True)
    payslip_date_from = fields.Date(related="payslip_id.date_from", store=True)


class HrPayslipLine(models.Model):
    _inherit = "hr.payslip.line"

    payslip_date_from = fields.Date(related="slip_id.date_from", store=True)
    payslip_state = fields.Selection(related="slip_id.state", store=True, readonly=True)


class HrPayslip(models.Model):
    _inherit = "hr.payslip"

    line_ids = fields.One2many(
        domain=[("salary_rule_id.appears_on_payslip", "=", True)]
    )

    def _create_worked_day_line(self, name, code, days, hours, contract_id):
        return {
            "name": name,
            "code": code,
            "number_of_days": days,
            "number_of_hours": hours,
            "contract_id": contract_id,
        }

    @api.multi
    def cancel_only_payslip(self):
        """ action_payslip_cancel attempts to cancel accounting moves which isn't necessary """
        self.write({"state": "cancel"})

    @api.model
    def get_worked_day_lines(self, contract_ids, date_from, date_to, context=None):
        # description, code
        MISC_WORKED_DAYS_LINES = [
            ("Vacaciones Automaticas", "VACAC"),
            ("Vaca Dias No Habiles", "VACAH"),
            ("Vacaciones", "VACAS"),
            ("Vacaciones Pagadas", "VACAP"),
            ("Incapacidades Asumidas", "INC_117"),
            ("Incapacidades Asumidias dias ATEP", "INC_117_ATEP"),
            ("Incapacidad Enfermedad General", "INC_123"),
            ("Incapacidad Enf Hos", "INC_123H"),
            ("Incapacidad Accidente de Trabajo", "INC_125"),
            ("Incapacidad Enfermedad Profesional", "INC_127"),
            ("Incapacidad Por Maternidad / Paternidad", "INC_129"),
            ("Prorroga Incapacidad", "INC_130"),
            ("Prórroga Incapacidad Accidente de Trabajo", "INC_130P"),
            ("Sanciones Laboral", "SLN_209"),
            ("Permiso No Remunerado", "SLN_217"),
            ("Liquidacion Cesantias", "L_CESANT"),
            ("Liquidacion Interes De Cesantias", "L_INT_CESANT"),
            ("Liquidacion Prima", "L_PRIMA"),
            ("Liquidacion Vacaciones", "LVACA"),
            ("Dias de Indemnizacion", "INDEM"),
            ("Hora Extra Diurna Ordinaria", "H_102"),
            ("Hora Extra Nocturna Ordinaria", "H_103"),
            ("Hora Extra Diurna Festiva", "H_104"),
            ("Hora Extra Festiva Nocturna", "H_105"),
            ("Dominicales y Festivos", "H_106"),
            ("Recargo Nocturno", "H_107"),
            ("Descanso en Dinero", "H_108"),
            ("Hora Extra Diurna Festiva Salario Variable", "H_120"),
            ("Hora Extra Nocturna Festiva Salario Variable", "H_121"),
            ("Dominicales y Festivos Reforma 2003", "H_141"),
            ("Licencia Remunerada", "I_152"),
            ("Ausencias Laborales", "I_206"),
            ("Sanciones Laborales", "I_209"),
            ("Ajuste de dias VAC mes siguiente (Autoliquidacion)", "AUT_VACA_MS"),
            ("Ajuste de dias VAC mes anterior (Autoliquidacon)", "AUT_VACA_MA"),
        ]
        res = []

        for contract in contract_ids:
            contract_id = contract.id
            res.append(
                self._create_worked_day_line(
                    _("Normal Working Days paid at 100%"),
                    "WORK100",
                    30,
                    240,
                    contract_id,
                )
            )
            for line in MISC_WORKED_DAYS_LINES:
                res.append(
                    self._create_worked_day_line(line[0], line[1], 0, 0, contract_id)
                )

            # keep order as defined here. hr.payslip.worked_days is ordered on 'payslip_id, sequence'
            for index, line in enumerate(res):
                line["sequence"] = index

        return res

    @api.multi
    def _create_payment_for_payslip(self):
        for payslip in self:
            payment_method_id = self.env.ref(
                "account_check_printing.account_payment_method_check"
            )
            if payslip.employee_id.bank_account_id:
                payment_method_id = self.env.ref(
                    "account.account_payment_method_manual_out"
                )

            self.env["account.payment"].create(
                {
                    "payment_type": "outbound",
                    "partner_type": "supplier",
                    "partner_id": payslip.employee_id.address_home_id.id,
                    "force_account_id": payslip.employee_id.address_home_id.property_account_payable_id.id,
                    "journal_id": self.env.user.company_id.payment_journal_id.id,
                    "payment_method_id": payment_method_id.id,
                    "communication": payslip.number,
                    "currency_id": payslip.journal_id.currency_id.id
                    or self.company_id.currency_id.id,
                    "amount": payslip.line_ids.filtered(
                        lambda line: line.code == "NET"
                    )[0].amount,
                    "payslip_id": payslip.id,
                }
            )

    @api.multi
    def action_payslip_done(self):
        for slip in self:
            line_ids = []
            debit_sum = 0.0
            credit_sum = 0.0
            date = slip.date or slip.date_to
            currency = slip.company_id.currency_id

            name = _("Payslip of %s") % (slip.employee_id.name)
            move_dict = {
                "narration": name,
                "ref": slip.number,
                "journal_id": slip.journal_id.id,
                "date": date,
            }
            for line in slip.details_by_salary_rule_category:
                amount = currency.round(slip.credit_note and -line.total or line.total)
                if currency.is_zero(amount):
                    continue

                debit_account_id = line.salary_rule_id.account_debit
                credit_account_id = line.salary_rule_id.account_credit

                debit_accounting_partner = slip.employee_id.address_home_id.id
                credit_accounting_partner = slip.employee_id.address_home_id.id
                if line.salary_rule_id.debit_accounting_partner:
                    debit_accounting_partner = line.contract_id[
                        line.salary_rule_id.debit_accounting_partner
                    ]
                    debit_accounting_partner = (
                        debit_accounting_partner.commercial_partner_id.id
                        if debit_accounting_partner
                        else False
                    )
                if line.salary_rule_id.credit_accounting_partner:
                    credit_accounting_partner = line.contract_id[
                        line.salary_rule_id.credit_accounting_partner
                    ]
                    credit_accounting_partner = (
                        credit_accounting_partner.commercial_partner_id.id
                        if credit_accounting_partner
                        else False
                    )

                include_taxes = any(
                    slip.line_ids.filtered(lambda line: line.code == "IMP_RTEFUENTE")
                )

                if debit_account_id:
                    debit_line = (
                        0,
                        0,
                        {
                            "name": line.name,
                            "partner_id": debit_accounting_partner,
                            "account_id": debit_account_id.id,
                            "journal_id": slip.journal_id.id,
                            "date": date,
                            "debit": amount > 0.0 and amount or 0.0,
                            "credit": amount < 0.0 and -amount or 0.0,
                            "analytic_account_id": line.salary_rule_id.analytic_account_id.id,
                            "tax_ids": [(6, 0, debit_account_id.tax_ids.ids)]
                            if include_taxes
                            else [],
                        },
                    )

                    if include_taxes:
                        debit_line[2]["tax_line_id"] = (
                            line.salary_rule_id.account_tax_id
                            and line.salary_rule_id.account_tax_id.id
                            or False
                        )

                    line_ids.append(debit_line)
                    debit_sum += debit_line[2]["debit"] - debit_line[2]["credit"]

                if credit_account_id:
                    credit_line = (
                        0,
                        0,
                        {
                            "name": line.name,
                            "partner_id": credit_accounting_partner,
                            "account_id": credit_account_id.id,
                            "journal_id": slip.journal_id.id,
                            "date": date,
                            "debit": amount < 0.0 and -amount or 0.0,
                            "credit": amount > 0.0 and amount or 0.0,
                            "analytic_account_id": line.salary_rule_id.analytic_account_id.id,
                            "tax_ids": [(6, 0, credit_account_id.tax_ids.ids)]
                            if include_taxes
                            else [],
                        },
                    )
                    line_ids.append(credit_line)
                    credit_sum += credit_line[2]["credit"] - credit_line[2]["debit"]

            # generate account.payments
            self._create_payment_for_payslip()

            if currency.compare_amounts(credit_sum, debit_sum) == -1:
                acc_id = slip.journal_id.default_credit_account_id.id
                if not acc_id:
                    raise UserError(
                        _(
                            'The Expense Journal "%s" has not properly configured the Credit Account!'
                        )
                        % (slip.journal_id.name)
                    )
                adjust_credit = (
                    0,
                    0,
                    {
                        "name": _("Adjustment Entry"),
                        "partner_id": False,
                        "account_id": acc_id,
                        "journal_id": slip.journal_id.id,
                        "date": date,
                        "debit": 0.0,
                        "credit": currency.round(debit_sum - credit_sum),
                    },
                )
                line_ids.append(adjust_credit)

            elif currency.compare_amounts(debit_sum, credit_sum) == -1:
                acc_id = slip.journal_id.default_debit_account_id.id
                if not acc_id:
                    raise UserError(
                        _(
                            'The Expense Journal "%s" has not properly configured the Debit Account!'
                        )
                        % (slip.journal_id.name)
                    )
                adjust_debit = (
                    0,
                    0,
                    {
                        "name": _("Adjustment Entry"),
                        "partner_id": False,
                        "account_id": acc_id,
                        "journal_id": slip.journal_id.id,
                        "date": date,
                        "debit": currency.round(credit_sum - debit_sum),
                        "credit": 0.0,
                    },
                )
                line_ids.append(adjust_debit)
            move_dict["line_ids"] = line_ids
            move = self.env["account.move"].create(move_dict)
            slip.write({"move_id": move.id, "date": date})
            move.post()

        return self.write({"paid": True, "state": "done"})


class HrContract(models.Model):
    _inherit = "hr.contract"

    social_security_accounting_partner_id = fields.Many2one(
        "res.partner", string="Sistema General de Seguridad Social en Salud"
    )
    pension_accounting_partner_id = fields.Many2one(
        "res.partner", string="Sistema General de Pensiones"
    )
    occupational_risks_accounting_partner_id = fields.Many2one(
        "res.partner", string="Sistema General de Riesgos Laborales"
    )
    family_compensation_accounting_partner_id = fields.Many2one(
        "res.partner", string="Cajas de Compensación Familiar"
    )
    icbf_accounting_partner_id = fields.Many2one(
        "res.partner", string="ICBF: El Instituto Colombiano de Bienestar Familiar"
    )
    sena_accounting_partner_id = fields.Many2one(
        "res.partner", string="SENA: El Servicio Nacional de Aprendizaje"
    )
    men_accounting_partner_id = fields.Many2one(
        "res.partner", string="MEN: El Ministerio de Educación Nacional"
    )
    esap_accounting_partner_id = fields.Many2one(
        "res.partner", string="ESAP: La escuela de Administración Pública"
    )
    administrator_accounting_partner_id = fields.Many2one(
        "res.partner", string="Fondo de Cesantías"
    )
    order_accounting_partner_id = fields.Many2one("res.partner", string="Libranza")
    complementary_plan_accounting_partner_id = fields.Many2one(
        "res.partner", string="Plan Complementario"
    )

    mobility_benefit_amount = fields.Float(string="Beneficio de Movilidad")
    food_benefit_amount = fields.Float(string="Apoyo de Alimentación")
    arl_type = fields.Selection(
        [
            ("0", "0.00%"),
            ("I", "0.522%"),
            ("II", "1.044%"),
            ("III", "2.436%"),
            ("IV", "4.35 %"),
            ("V", "6.96 %"),
        ],
        string="Tipo de ARL - Nivel de Riesgo",
    )
    pension_status = fields.Boolean("Empleado Pensionado")
    quotient_type = fields.Selection(
        [
            ("01", "Dependiente"),
            ("12", "Aprendices en Etapa Lectiva"),
            ("19", "Aprendices en etapa productiva"),
        ],
        string="Tipo de  Cotizante",
    )
    quotient_subtype = fields.Selection(
        [
            ("00", "No Aplica"),
            ("01", "Dependiente pensionado por vejez activo"),
            ("02", "Independiente pensionado por vejez activo"),
            ("03", "Cotizante no obligado a cotizar pensiones por edad"),
            ("04", "Cotizante con requisitos cumplidos para pensión"),
            (
                "05",
                "Cotizante a quien se le ha reconocido indemnización sustituta o devolución de saldos",
            ),
            (
                "06",
                "Cotizante perteneciente a un régimen exceptuado o a entidades autorizadas para recibir aportes exclusivamente de un grupo de sus propios trabajadores",
            ),
        ],
        string="Sub Tipo de Cotizante",
    )


class HrSalaryRule(models.Model):
    _inherit = "hr.salary.rule"

    def _get_accounting_partner_values(self):
        Fields = self.env["ir.model.fields"]
        # do this because Odoo doesn't always delete ir.model.fields
        loaded_hr_contract_fields = list(self.env["hr.contract"]._fields.keys())
        contract_accounting_partner_fields = Fields.search(
            [
                ("model", "=", "hr.contract"),
                ("name", "like", "_accounting_partner_id"),
                ("name", "in", loaded_hr_contract_fields),
            ],
            order="field_description",
        )
        return [
            (field.name, field.field_description)
            for field in contract_accounting_partner_fields
        ]

    debit_accounting_partner = fields.Selection(
        "_get_accounting_partner_values",
        help="Selects partner used for debit accounting entries. If empty the accounts on the partner linked to the employee will be used.",
    )
    credit_accounting_partner = fields.Selection(
        "_get_accounting_partner_values",
        help="Selects partner used for credit accounting entries. If empty the accounts on the partner linked to the employee will be used.",
    )
    print_on_payslip_report = fields.Boolean(
        string="Imprimir en Comprobante", default=True
    )
    associated_leave_type_id = fields.Many2one(
        "hr.leave.type", string="Associated leave type"
    )
    reporting_label = fields.Selection(
        [
            ("pagoe", "Pagoe"),
            ("cespag", "Cespag"),
            ("grep", "Grep"),
            ("peninv", "Peninv"),
            ("ingremple", "Ingremple"),
            ("aporsal", "Aporsal"),
            ("aportepen", "Aportepen"),
            ("aporpens", "Aporpens"),
            ("vretemp", "Vretemp"),
        ],
        string="Etiqueta Reporte Retención",
    )


class HrHolidays(models.Model):
    _inherit = "hr.leave"

    authorization_number = fields.Char(string="Número de Autorización")
    initial_authorization_number = fields.Char(string="Número de Autorización Inicial")
    average_salary = fields.Float(string="Salario Promedio")
    total_salary = fields.Float(string="Total Pagado")


class HrHolidaysStatus(models.Model):
    _inherit = "hr.leave.type"

    leave_type_code = fields.Selection(
        [
            ("ING", "ING - Ingreso"),
            ("RET", "RET - Retiro"),
            (
                "TDE",
                "TDE - Traslado desde otra EPS (Entidad Promotora de Salud) o EOC (Entidades Obligadas a Compensar)",
            ),
            (
                "TAE",
                "TAE - Traslado a otra EPS o EOC (Entidades Obligadas a Compensar)",
            ),
            ("TDP", "TDP - Traslado desde otra Administradora de Pensiones"),
            ("TAP", "TAP - Traslado a otra Administradora de Pensiones"),
            (
                "SLN",
                "SLN - Suspensión temporal del contrato de trabajo o licencia no remunerada o comisión de servicios",
            ),
            ("IGE", "IGE - Incapacidad Temporal por Enfermedad General"),
            ("LMA", "LMA - Licencia de Maternidad o de paternidad"),
            ("VAC", "VAC - Vacaciones"),
            ("LR", "LR - Licencia Remunerada"),
            (
                "IRP",
                "IRP - Incapacidad por accidente de trabajo o enfermedad profesional",
            ),
            ("VSP", "VSP - Variación permanente de salario"),
            ("NA", "NA - No Aplica"),
        ],
        string="Código de Novedad (Autoliquidacion)",
    )
    salary_rule_ids = fields.One2many("hr.salary.rule", "associated_leave_type_id")


class ResPartner(models.Model):
    _inherit = "res.partner"

    administration_code = fields.Char(string="Codigo de Administradora")
    name1 = fields.Char(string="Name 1")
    name2 = fields.Char(string="Name 2")
    name3 = fields.Char(string="Name 3")
    last_name1 = fields.Char(string="Last Name 1")
    last_name2 = fields.Char(string="Last Name 2")

    @api.multi
    def _get_document_code(self):
        self.ensure_one()
        type_to_code = {
            "id_document": "CC",
            "foreign_id_card": "CE",
            "external_id": "NA",
            "passport": "PA",
            "rut": "NI",
            "id_card": "TI",
        }

        res = type_to_code.get(self.l10n_co_document_type)
        if not res:
            raise UserError(_("No Document Type defined for %s.") % self.display_name)

        return res


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    company_id = fields.Many2one(
        "res.company", string="Company", default=lambda self: self.env.user.company_id
    )
    payment_journal_id = fields.Many2one(
        "account.journal",
        related="company_id.payment_journal_id",
        string="Método de Pago por defecto",
        readonly=False,
    )

    @api.onchange("company_id")
    def onchange_company_id(self):
        if self.company_id:
            self.payment_journal_id = self.company_id.payment_journal_id


class ResCompany(models.Model):
    _inherit = "res.company"

    payment_journal_id = fields.Many2one(
        "account.journal", string="Journal used for payments generated from payslips."
    )


class ResCity(models.Model):
    _inherit = "res.city"

    code = fields.Char(string=u"Código de Ciudad")


class AccountPayment(models.Model):
    _inherit = "account.payment"

    payslip_id = fields.Many2one("hr.payslip", string="Payslip")

    @api.multi
    def post(self):
        res = super(AccountPayment, self).post()

        for payment in self.filtered("payslip_id"):
            amls_to_reconcile = (
                payment.move_line_ids | payment.payslip_id.move_id.line_ids
            )
            amls_to_reconcile = amls_to_reconcile.filtered(
                lambda line: line.account_id
                == payment.partner_id.property_account_payable_id
                and line.partner_id == payment.partner_id
            )
            amls_to_reconcile.reconcile()

        return res
