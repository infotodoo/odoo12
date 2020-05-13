# coding: utf-8
# Copyright (C) 2019 Odoo Inc
from odoo import api, fields, models, _
from odoo.exceptions import ValidationError
from datetime import date, datetime
import base64
import math


class AutoliquidacionReportWizard(models.TransientModel):
    _name = "co_payroll.autoliquidacion_report"
    _description = "Autoliquidacion Report Wizard"

    plan_type = fields.Selection(
        [("1", "Electronica"), ("2", "Asistida")],
        string="Modalidad de Planilla",
        default="1",
        required=True,
    )
    presentation_type = fields.Char(
        default="U", string="Forma de Presentación", required=True
    )

    payslip_date_start = fields.Date(required=True)
    payslip_date_end = fields.Date(required=True)
    report_date_start = fields.Date(required=True)
    report_date_end = fields.Date(required=True)

    provider_type = fields.Integer(
        required=True, string="Tipo de Contribuyente", default=1
    )
    information_operator_code = fields.Integer(
        required=True, string="Codigo del Operador", default=83
    )
    registration_type = fields.Char(
        required=True, string="Tipo de Registros", default="01"
    )  # todo jov

    def _get_payslips(self):
        return self.env["hr.payslip"].search(
            [
                ("date_from", "=", self.payslip_date_start),
                ("date_to", "=", self.payslip_date_end),
            ]
        )

    def _get_leaves(self, employee, holiday_statuses=False, codes=False):
        if not isinstance(codes, (list, tuple)):
            codes = (codes,)

        domain = [
            ("employee_id", "=", employee.id),
            ("state", "=", "validate"),
            ("date_from", ">=", self.payslip_date_start),
            ("date_from", "<=", self.payslip_date_end),
        ]

        if any(codes):
            domain += [("holiday_status_id.leave_type_code", "in", codes)]
        if holiday_statuses:
            domain += [("holiday_status_id", "in", holiday_statuses.ids)]

        return self.env["hr.leave"].search(domain)

    def _find_start_first_leave(self, employee, code):
        all_leaves = self._get_leaves(employee, codes=code)
        return all_leaves[0].date_from if all_leaves else None

    def _find_end_first_leave(self, employee, code):
        all_leaves = self._get_leaves(employee, codes=code)
        return all_leaves[0].date_to if all_leaves else None

    def _get_leaves_needing_separate_lines(self, employee):
        LEAVE_TYPES = ("VAC", "LR", "IGE", "LMA", "SLN", "IRP", "RET")
        return self._get_leaves(employee, codes=LEAVE_TYPES)

    def _get_line_total(self, payslip, code):
        matching_lines = payslip.line_ids.filtered(lambda l: l.code == code)
        return abs(matching_lines[0].total) if matching_lines else 0

    def _get_percentage(self, payslip, code):
        return sum(
            payslip.line_ids.filtered(
                lambda l: l.code in (code, "{}_AD".format(code))
            ).mapped(lambda l: l.rate / 100.0)
        )

    def _get_number_of_worked_days(self, payslip):
        return abs(
            payslip.worked_days_line_ids.filtered(
                lambda l: l.code == "WORK100"
            ).number_of_days
        )

    def _round_to_nearest(self, value, nearest):
        """ Round ``value`` up to ``nearest``. E.g. rounding 3211 to nearest 100 gives 3300. """
        assert nearest > 0
        return int(math.ceil(value / float(nearest)) * nearest)

    def _get_arl_value(self, contract):
        ARL_TYPE_TO_VALUE = {
            "0": 0.0000000,
            "I": 0.0052200,
            "II": 0.0104400,
            "III": 0.0243600,
            "IV": 0.0435000,
            "V": 0.0696000,
        }
        return ARL_TYPE_TO_VALUE[contract.arl_type]

    def _get_arl_number(self, contract):
        ARL_TYPE_TO_NUMBER = {
            "0": " ",
            "I": "1",
            "II": "2",
            "III": "3",
            "IV": "4",
            "V": "5",
        }
        return ARL_TYPE_TO_NUMBER[contract.arl_type]

    def _get_work_center(self, contract):
        ARL_TYPE_TO_WORK_CENTER = {
            "0": 0,  # TBD in spec
            "I": 2,
            "II": 0,  # TBD in spec
            "III": 1,
            "IV": 0,  # TBD in spec
            "V": 5,
        }
        return ARL_TYPE_TO_WORK_CENTER[contract.arl_type]

    def _format_string(self, string, length):
        # strings should be formatted as follows:
        # - left aligned
        # - padded with spaces to the right
        # - truncated to length to ensure the lines stay the correct length
        return ("{" + ":<{length}.{length}".format(length=length) + "}").format(
            string or ""
        )

    def _format_number(self, number, length):
        # numbers should be formatted as follows:
        # - right aligned
        # - padded with zeroes to the left
        return ("{" + ":>0{}.0f".format(length) + "}").format(number)

    def _format_float(self, number, length):
        # floats only appear in the file in the following range 0 <=
        # number <= 1. So we can just format with a single leading 0.
        assert length > 2, "Can't format a float with a length < 2"
        return ("{" + ":>01.{}f".format(length - 2) + "}").format(number)

    def _format_datetime(self, dt):
        if not dt:
            return " " * len("YYYY-MM-DD")

        if isinstance(dt, date):
            dt = datetime(dt.year, dt.month, dt.day)

        # dates should be formatted as YYYY-MM-DD
        dt = fields.Datetime.context_timestamp(
            self, dt
        )  # convert from UTC to user's timezone
        return dt.strftime("%Y-%m-%d")

    def _format_datetime_for_leave(
        self, employee, leave, leave_type_code, start_first_leave
    ):
        if leave and leave.holiday_status_id.leave_type_code == leave_type_code:
            if start_first_leave:
                return self._format_datetime(
                    self._find_start_first_leave(employee, leave_type_code)
                )
            else:
                return self._format_datetime(
                    self._find_end_first_leave(employee, leave_type_code)
                )
        else:
            return self._format_datetime(None)

    def _get_hours_for_worked_days_with_codes(self, payslip, codes):
        if not isinstance(codes, (list, tuple)):
            codes = (codes,)
        return sum(
            payslip.worked_days_line_ids.filtered(lambda l: l.code in codes).mapped(
                "number_of_hours"
            )
        )

    def _generate_header(self):
        header = ""

        header += self._format_string(self.registration_type, 2)
        header += self._format_string(self.plan_type, 1)
        header += "0001"
        header += self._format_string(self.env.user.company_id.partner_id.name, 200)
        header += self._format_string(
            self.env.user.company_id.partner_id._get_document_code(), 2
        )
        header += self._format_string(self.env.user.company_id.partner_id.vat, 17)
        header += "E"
        header += self._format_string("", 10)
        header += self._format_string("", 10)
        header += self._format_string(self.presentation_type, 1)
        header += self._format_string("", 10)
        header += self._format_string("", 40)
        header += self._format_string(
            self.env.user.company_id.partner_id.administration_code, 6
        )

        # todo jov: raise if payslip_date_start and payslip_date_end aren't in the same month?
        payslip_date = self.payslip_date_start
        header += self._format_string(payslip_date.strftime("%Y-%m"), 7)

        reporting_date = self.report_date_start
        header += self._format_string(reporting_date.strftime("%Y-%m"), 7)

        header += self._format_number(0, 10)
        header += self._format_string("", 10)

        payslips = self._get_payslips()
        header += self._format_number(len(payslips.mapped("employee_id")), 5)

        # field 20, this will be filled in after generating the whole file
        header += self._format_number(0, 12)

        header += self._format_number(self.provider_type, 2)
        header += self._format_number(self.information_operator_code, 2)
        header += "\r\n"

        return header

    def _generate_line(self, index, payslip, leave):
        line = ""
        employee = payslip.employee_id
        partner = employee.address_home_id
        contract = employee.contract_id
        document_code = partner._get_document_code()

        line += "02"  # field 1
        line += self._format_number(index, 5)
        line += self._format_string(document_code, 2)
        line += self._format_string(partner._get_vat_without_verification_code(), 16)
        line += self._format_string(contract.quotient_type, 2)
        line += self._format_string(contract.quotient_subtype, 2)  # field 6
        line += "X" if document_code in ("CE", "PA", "CD") else " "
        line += " "
        line += self._format_string(partner.city_id.state_id.code, 2)
        line += self._format_string(partner.city_id.code, 3)  # 10
        line += self._format_string(partner.last_name1, 20)
        line += self._format_string(partner.last_name2, 30)
        line += self._format_string(partner.name1, 20)
        line += self._format_string(partner.name2, 30)
        contract_start = self.env["hr.contract"].search(
            [
                ("id", "=", contract.id),  # field 15
                ("date_start", ">=", self.payslip_date_start),
                ("date_start", "<=", self.payslip_date_end),
            ]
        )
        line += "X" if contract_start else " "
        contract_end = self.env["hr.contract"].search(
            [
                ("id", "=", contract.id),  # field 16
                ("date_end", ">=", self.payslip_date_start),
                ("date_end", "<=", self.payslip_date_end),
            ]
        )
        line += "X" if contract_end else " "
        line += "X" if self._get_leaves(employee, codes="TDE") else " "
        line += "X" if self._get_leaves(employee, codes="TAE") else " "
        line += "X" if self._get_leaves(employee, codes="TDP") else " "
        line += "X" if self._get_leaves(employee, codes="TAP") else " "  # 20
        line += "X" if self._get_leaves(employee, codes="VSP") else " "
        line += " "

        # 23
        if (
            leave
            or self._get_line_total(payslip, "IBC_L") == contract.wage
            or "SAL_INT" in contract.struct_id.code
            or "APR" in contract.struct_id.code
        ):
            line += " "
        else:
            line += "X"

        line += (
            "X"
            if leave
            and leave.holiday_status_id.leave_type_code == "SLN"
            and self._get_leaves(employee, codes="SLN")
            else " "
        )
        line += (
            "X"
            if leave
            and leave.holiday_status_id.leave_type_code == "IGE"
            and self._get_leaves(employee, codes="IGE")
            else " "
        )
        line += (
            "X"
            if leave
            and leave.holiday_status_id.leave_type_code == "LMA"
            and self._get_leaves(employee, codes="LMA")
            else " "
        )
        line += (
            "X"
            if leave
            and leave.holiday_status_id.leave_type_code == "VAC"
            and self._get_leaves(employee, codes="VAC")
            else "L"
            if leave
            and leave.holiday_status_id.leave_type_code == "LR"
            and self._get_leaves(employee, codes="LR")
            else " "
        )
        line += " "
        line += " "

        # field 30
        if leave and leave.holiday_status_id.leave_type_code == "IRP":
            line += self._format_number(abs(leave.number_of_days), 2)
        else:
            line += self._format_number(0, 2)

        line += self._format_string(
            contract.pension_accounting_partner_id.administration_code, 6
        )
        line += self._format_string(" ", 6)
        line += self._format_string(
            contract.social_security_accounting_partner_id.administration_code, 6
        )
        line += self._format_string(" ", 6)
        line += self._format_string(
            contract.family_compensation_accounting_partner_id.administration_code, 6
        )

        # field 36, 37, 38, 39
        if not leave:
            if contract.quotient_subtype in ("01", "02"):
                line += self._format_number(0, 2)
            else:
                line += self._format_number(
                    self._get_number_of_worked_days(payslip)
                    if "APR_E" not in contract.struct_id.code
                    else 0,
                    2,
                )
            line += self._format_number(self._get_number_of_worked_days(payslip), 2)
            line += self._format_number(
                self._get_number_of_worked_days(payslip)
                if "APR_EL" not in contract.struct_id.code
                else 0,
                2,
            )
            line += self._format_number(
                self._get_number_of_worked_days(payslip)
                if "APR_E" not in contract.struct_id.code
                else 0,
                2,
            )
        else:
            if contract.quotient_subtype in ("01", "02"):
                line += self._format_number(0, 2)
            else:
                line += self._format_number(
                    abs(leave.number_of_days)
                    if "APR_E" not in contract.struct_id.code
                    else 0,
                    2,
                )
            line += self._format_number(abs(leave.number_of_days), 2)
            line += self._format_number(
                abs(leave.number_of_days)
                if "APR_EL" not in contract.struct_id.code
                else 0,
                2,
            )
            line += self._format_number(
                abs(leave.number_of_days)
                if "APR_E" not in contract.struct_id.code
                else 0,
                2,
            )

        line += self._format_number(contract.wage, 9)
        line += "X" if contract.struct_id.code == "SAL_INT" else " "

        ibc_ccf = 0
        # fields 42, 43, 44, 45
        if not leave:
            ibc_total = self._get_line_total(payslip, "IBC_AUT")
            if contract.quotient_subtype in ("01", "02"):
                line += self._format_number(0, 9)
            else:
                line += self._format_number(
                    ibc_total if "APR_E" not in contract.struct_id.code else 0, 9
                )
            line += self._format_number(ibc_total, 9)
            line += self._format_number(
                ibc_total if "APR_EL" not in contract.struct_id.code else 0, 9
            )

            # exception for field 45, don't take IBC_AUT
            if contract.struct_id.code == "SAL_INT":
                ibc_ccf = self._get_line_total(payslip, "GROSS_70")
            else:
                ibc_ccf = sum(
                    payslip.line_ids.filtered(
                        lambda l: l.category_id.code in ("ING", "HOR", "MAYVAL")
                    ).mapped("total")
                )

            line += self._format_number(ibc_ccf, 9)
        else:
            total_days = sum(
                self._get_leaves(
                    employee, holiday_statuses=leave.holiday_status_id
                ).mapped(lambda l: abs(l.number_of_days))
            )
            if leave.holiday_status_id.leave_type_code == "VAC":
                ibc_total = self._get_line_total(payslip, "IBC_AUT_VACA") * (
                    abs(leave.number_of_days) / total_days
                )
            else:
                if not leave.holiday_status_id.salary_rule_ids:
                    raise ValidationError(
                        _(
                            "There should be some salary rules associated to the %s leave type (id: %s)"
                            % (leave.holiday_status_id.name, leave.holiday_status_id.id)
                        )
                    )
                ibc_total = sum(
                    [
                        self._get_line_total(payslip, code)
                        for code in leave.holiday_status_id.salary_rule_ids.mapped(
                            "code"
                        )
                    ]
                ) * (abs(leave.number_of_days) / total_days)
            if contract.quotient_subtype in ("01", "02"):
                line += self._format_number(0, 9)
            else:
                line += self._format_number(
                    ibc_total if "APR_E" not in contract.struct_id.code else 0, 9
                )
            line += self._format_number(ibc_total, 9)
            line += self._format_number(
                ibc_total if "APR_EL" not in contract.struct_id.code else 0, 9
            )

            ibc_ccf = ibc_total
            line += self._format_number(ibc_ccf, 9)

        pension_rate = self._get_percentage(payslip, "201") + self._get_percentage(
            payslip, "AP_PENSION"
        )
        line += self._format_float(pension_rate, 7)  # field 46

        # field 47
        if (
            leave
            and leave.holiday_status_id.leave_type_code == "VAC"
            and "LVACA" in payslip.line_ids.mapped("code")
        ):
            field_47_value = 0
        else:
            field_47_value = self._round_to_nearest(ibc_total * pension_rate, 100)
        line += self._format_number(field_47_value, 9)

        field_48_value = 0
        line += self._format_number(field_48_value, 9)  # field 48
        field_49_value = 0
        line += self._format_number(field_49_value, 9)
        line += self._format_number(field_47_value + field_48_value + field_49_value, 9)

        line += self._format_number(
            self._round_to_nearest(
                self._get_line_total(payslip, "aut_solidaridad_sol"), 100
            ),
            9,
        )  # field 51
        line += self._format_number(
            self._round_to_nearest(
                self._get_line_total(payslip, "aut_solidaridad_subs"), 100
            ),
            9,
        )

        line += self._format_number(0, 9)

        healthcare_rate = self._get_percentage(payslip, "200") + self._get_percentage(
            payslip, "AP_SAL"
        )
        line += self._format_float(healthcare_rate, 7)

        if (
            leave
            and leave.holiday_status_id.leave_type_code == "VAC"
            and "LVACA" in payslip.line_ids.mapped("code")
        ):  # field 55
            line += self._format_number(0, 9)
        else:
            line += self._format_number(
                self._round_to_nearest(ibc_total * healthcare_rate, 100), 9
            )

        line += self._format_number(0, 9)
        line += self._format_string("", 15)
        line += self._format_number(0, 9)
        line += self._format_string("", 15)
        line += self._format_number(0, 9)
        job_risk_rate = self._get_arl_value(contract)
        line += (
            self._format_float(job_risk_rate, 9)
            if not leave
            else self._format_float(0, 9)
        )  # field 61
        line += self._format_number(self._get_work_center(contract), 9)
        line += (
            self._format_number(
                self._round_to_nearest(ibc_total * job_risk_rate, 100), 9
            )
            if not leave
            else self._format_number(0, 9)
        )

        ccf_rate = self._get_percentage(payslip, "APORTE_CAJA_COMP")
        line += (
            self._format_float(ccf_rate, 7)
            if not leave or leave.holiday_status_id.leave_type_code in ("VAC", "LR")
            else self._format_float(0, 7)
        )

        # field 65
        if leave and leave.holiday_status_id.leave_type_code in (
            "IGE",
            "LMA",
            "SLN",
            "IRP",
        ):
            line += self._format_number(0, 9)
        else:
            line += self._format_number(
                self._round_to_nearest(ibc_ccf * ccf_rate, 100), 9
            )

        # field 66, 67
        if not leave or leave.holiday_status_id.leave_type_code in ("VAC", "LR"):
            sena_rate = self._get_percentage(payslip, "AP_SENA")
            line += self._format_float(sena_rate, 7)
            line += self._format_number(
                self._round_to_nearest(ibc_ccf * sena_rate, 100), 9
            )
        else:
            line += self._format_float(0, 7)
            line += self._format_number(0, 9)

        # field 68, 69
        if not leave or leave.holiday_status_id.leave_type_code in ("VAC", "LR"):
            icbf_rate = self._get_percentage(payslip, "AP_ICFB")
            line += self._format_float(icbf_rate, 7)
            line += self._format_number(
                self._round_to_nearest(ibc_ccf * icbf_rate, 100), 9
            )
        else:
            line += self._format_float(0, 7)
            line += self._format_number(0, 9)

        line += self._format_float(0, 7)
        line += self._format_number(0, 9)
        line += self._format_float(0, 7)
        line += self._format_number(0, 9)
        line += self._format_string(" ", 2)
        line += self._format_string(" ", 16)

        if (
            payslip.line_ids.filtered(lambda l: l.code == "COND_APORT_EMP").amount
            > payslip.line_ids.filtered(lambda l: l.code == "SMLMV_10").amount
            or "SAL_INT" in contract.struct_id.code
            or "APR" in contract.struct_id.code
        ):
            field_76 = "N"
        else:
            field_76 = "S"

        line += field_76
        line += self._format_string(
            contract.occupational_risks_accounting_partner_id.administration_code, 6
        )
        line += self._get_arl_number(contract)  # field 78
        line += " "
        line += (
            self._format_datetime(contract.date_start)
            if contract_start
            else self._format_datetime(self._find_start_first_leave(employee, "ING"))
        )
        line += (
            self._format_datetime(contract.date_end)
            if contract_end
            else self._format_datetime(self._find_start_first_leave(employee, "RET"))
        )  # field 81
        line += self._format_datetime(self._find_start_first_leave(employee, "VSP"))
        line += self._format_datetime_for_leave(
            employee, leave, "SLN", start_first_leave=True
        )  # field 83
        line += self._format_datetime_for_leave(
            employee, leave, "SLN", start_first_leave=False
        )
        line += self._format_datetime_for_leave(
            employee, leave, "IGE", start_first_leave=True
        )
        line += self._format_datetime_for_leave(
            employee, leave, "IGE", start_first_leave=False
        )
        line += self._format_datetime_for_leave(
            employee, leave, "LMA", start_first_leave=True
        )  # field 87
        line += self._format_datetime_for_leave(
            employee, leave, "LMA", start_first_leave=False
        )

        if leave and leave.holiday_status_id.leave_type_code == "VAC":
            line += self._format_datetime(self._find_start_first_leave(employee, "VAC"))
            line += self._format_datetime(self._find_end_first_leave(employee, "VAC"))
        elif leave and leave.holiday_status_id.leave_type_code == "LR":
            line += self._format_datetime(self._find_start_first_leave(employee, "LR"))
            line += self._format_datetime(self._find_end_first_leave(employee, "LR"))
        else:
            line += self._format_datetime(None)
            line += self._format_datetime(None)

        line += self._format_datetime(None)  # field 91
        line += self._format_datetime(None)
        line += self._format_datetime_for_leave(
            employee, leave, "IRP", start_first_leave=True
        )
        line += self._format_datetime_for_leave(
            employee, leave, "IRP", start_first_leave=False
        )

        # field 95
        if field_76 == "S" or "APR" in contract.struct_id.code:
            line += self._format_number(0, 9)
        else:
            line += self._format_number(ibc_ccf, 9)

        # field 96
        if (
            "APR" in payslip.line_ids.mapped("code")
            or "APR_E" in contract.struct_id.code
        ):
            line += self._format_number(0, 3)
        elif not leave:
            line += self._format_number(
                self._get_hours_for_worked_days_with_codes(payslip, "WORK100"), 3
            )
        elif leave and leave.holiday_status_id.leave_type_code == "VAC":
            line += self._format_number(
                self._get_hours_for_worked_days_with_codes(
                    payslip, ("VACAC", "VACAH", "VACAS", "VACAP")
                ),
                3,
            )
        elif leave and leave.holiday_status_id.leave_type_code == "LR":
            line += self._format_number(
                self._get_hours_for_worked_days_with_codes(payslip, "I_152"), 3
            )
        else:
            line += self._format_number(0, 3)

        line += "\r\n"

        return line, ibc_ccf

    def _generate_lines(self):
        line_nr = 0
        lines = ""
        total_ibc_ccf = 0
        for payslip in self._get_payslips():
            if self._get_number_of_worked_days(payslip) > 0:
                line, ibc_ccf = self._generate_line(line_nr, payslip, None)
                lines += line
                total_ibc_ccf += ibc_ccf
                line_nr += 1

            for leave in self._get_leaves_needing_separate_lines(payslip.employee_id):
                line, ibc_ccf = self._generate_line(line_nr, payslip, leave)
                lines += line
                total_ibc_ccf += ibc_ccf
                line_nr += 1

        return lines, total_ibc_ccf

    @api.multi
    def generate(self):
        IrAttachment = self.env["ir.attachment"]
        ATTACHMENT_NAME = "autoliquidacion_report.txt"

        header = self._generate_header()
        lines, total_ibc_ccf = self._generate_lines()

        # fill field 20 in the header which is the sum of every field 45 in the lines
        # field 20 is at pos 343-354 (starting from 0)
        header = header[:343] + self._format_number(total_ibc_ccf, 12) + header[355:]

        file_content = header
        file_content += lines
        file_content = file_content.upper()

        # clean old attachment
        IrAttachment.search([("name", "=", ATTACHMENT_NAME)]).unlink()

        created_attachment = IrAttachment.create(
            {
                "name": ATTACHMENT_NAME,
                "datas": base64.encodestring(file_content.encode("utf-8")),
                "datas_fname": ATTACHMENT_NAME,
            }
        )

        return {
            "type": "ir.actions.act_url",
            "target": "self",
            "url": "/web/content/%s?download=1" % created_attachment.id,
        }
