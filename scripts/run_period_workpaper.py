"""Produce a provisional period CGT workpaper with explicit scope and exceptions.

Keep complete histories for every security disposed of in the requested period,
all parsed option lifecycles, and period income receipts. Unrelated failing
stock histories are not needed to calculate those disposals. This does not
certify that excluded broker events or fund classifications are settled.
"""
import copy
from dataclasses import replace
import datetime as dt
from decimal import Decimal
from importlib import resources
import json
from pathlib import Path
import csv

import typst
from cgt_calc.args_parser import create_parser, resolve_reporting_period
from cgt_calc.parsers.broker_registry import BrokerRegistry
from cgt_calc.currency_converter import CurrencyConverter
from cgt_calc.current_price_fetcher import CurrentPriceFetcher
from cgt_calc.isin_converter import IsinConverter
from cgt_calc.share_prices import SharePrices
from cgt_calc.spin_off_handler import SpinOffHandler
from cgt_calc.main import CapitalGainsCalculator
from cgt_calc.render_text import render_text
from cgt_calc.report_view import build_report_view
from cgt_calc.render_pdf import _report_data
from cgt_calc.const import TYPST_TEMPLATE_RESOURCE
from cgt_calc.resources import RESOURCES_PACKAGE

ROOT=Path(__file__).resolve().parents[1]


def main():
    import os
    os.chdir(ROOT)
    parser=create_parser()
    args=parser.parse_args(['--from','2025-12-01','--to','2026-04-05',
        '--schwab-file','data/input/schwab-clean/merged-schwab-transactions.csv',
        '--raw-file','data/input/raw/pre-residency-raw.csv','--no-balance-check',
        '--exchange-rates-file','data/out/exchange_rates.csv',
        '--isin-translation-file','data/out/isin_translation.csv',
        '--spin-offs-file','data/out/spin_offs.csv'])
    resolve_reporting_period(parser,args)
    start,end=dt.date(2025,12,1),dt.date(2026,4,5)
    isin=IsinConverter(args.isin_translation_file)
    transactions=BrokerRegistry.load_all_transactions(args,isin)
    disposal_actions={'SELL','FULL_REDEMPTION','CASH_MERGER','TRANSFER_TO_SPOUSE','GIFT','GIFT_UNCONNECTED','UNCLASSIFIED_GIFT'}
    symbols={t.symbol for t in transactions if start<=t.date<=end and t.action.name in disposal_actions}
    income_actions={'DIVIDEND','DIVIDEND_TAX','INTEREST','INTEREST_TAX','REINVEST_DIVIDENDS'}
    selected=[t for t in transactions if t.date<=end+dt.timedelta(days=30) and (t.symbol in symbols or t.option_contract is not None) or start<=t.date<=end and t.action.name in income_actions]
    excluded=[t for t in transactions if start<=t.date<=end and t not in selected]
    assert not any(t.action.name in disposal_actions|{'OPTION_GRANT'} for t in excluded)
    currency=CurrencyConverter(args.exchange_rates_file)
    prices=SharePrices(args.prices_file)
    calc=CapitalGainsCalculator(args.year,currency,isin,CurrentPriceFetcher(currency,historical_prices_data=prices.closing_prices()),SpinOffHandler(args.spin_offs_file),prices,args.interest_fund_tickers,balance_check=False,period_start=start,period_end=end)
    report=calc.calculate(copy.deepcopy(selected))
    output=ROOT/'data/out/period-report';output.mkdir(parents=True,exist_ok=True)
    notice=('PROVISIONAL WORKPAPER - not a final return calculation.\n'
            'Joint accounts, 1 December 2025 to 5 April 2026; ownership 50% each.\n'
            'All parsed period share/redemption disposals and written-option lifecycles included.\n'
            'Excluded broker actions and fund/corporate-action classifications remain unresolved.\n'
            'Standard calculator tax allowances are not the confirmed FIG claim treatment.\n\n')
    (output/'PROVISIONAL-cgt-report.txt').write_text(notice+render_text(report),encoding='utf-8')
    view=build_report_view(report)
    view=replace(view,title_period='PROVISIONAL - FIG relief not applied - '+report.title_period,income_summary=replace(view.income_summary,dividend_allowance=None,taxable_dividends=None))
    with resources.as_file(resources.files(RESOURCES_PACKAGE)) as root:
        pdf=typst.Compiler(root=root,font_paths=[root/'fonts'],ignore_system_fonts=True,sys_inputs={'view':_report_data(view)}).compile(input=root/TYPST_TEMPLATE_RESOURCE,format='pdf')
    (output/'PROVISIONAL-cgt-report.pdf').write_bytes(pdf)
    def money(value):return str(value.quantize(Decimal('.01')))
    figures={'period_start':str(start),'period_end':str(end),'status':'PROVISIONAL',
        'disposed_security_histories':len(symbols),'parsed_transactions':len(transactions),'included_transactions':len(selected),
        'option_grants_in_period':sum(start<=t.date<=end and t.action.name=='OPTION_GRANT' for t in selected),
        'joint':{'disposals':report.disposal_count,'proceeds_gbp':money(report.disposal_proceeds),'allowable_costs_gbp':money(report.allowable_costs),'gains_before_losses_gbp':money(report.capital_gain),'losses_gbp':money(report.capital_loss),'net_gain_gbp':money(report.total_gain()),'foreign_interest_gbp':money(report.total_foreign_interest),'uk_interest_gbp':money(report.total_uk_interest)},
        'each_owner_50_percent':{'gains_before_losses_gbp':money(report.capital_gain/2),'losses_gbp':money(report.capital_loss/2),'net_gain_gbp':money(report.total_gain()/2)},
        'notice':notice}
    with (output/'disposal-details.csv').open('w',newline='') as handle:
        writer=csv.writer(handle);writer.writerow(['date','symbol','kind','quantity','proceeds_gbp','cost_gbp','gain_gbp'])
        for day in view.days:
            for event in day.events:
                if event.proceeds is not None and event.gain is not None:
                    writer.writerow([day.date,event.symbol,event.kind,event.quantity,event.proceeds,event.proceeds-event.gain,event.gain])
    with (output/'ignored-reinvestment-income.csv').open('w',newline='') as handle:
        writer=csv.writer(handle);writer.writerow(['date','symbol','amount','currency','notice'])
        for t in selected:
            if start<=t.date<=end and t.action.name=='REINVEST_DIVIDENDS':writer.writerow([t.date,t.symbol,t.amount,t.currency,'Retained receipt ignored by engine; income classification unresolved'])
    (output/'working-figures.json').write_text(json.dumps(figures,indent=2))
    with (output/'period-events-outside-calculation.csv').open('w',newline='') as f:
        w=csv.writer(f);w.writerow(['date','symbol','action','quantity','amount','currency','source'])
        for t in excluded:w.writerow([t.date,t.symbol,t.action.name,t.quantity,t.amount,t.currency,str(t)])
    (output/'READ-ME.md').write_text(notice+'\nOpen PROVISIONAL-cgt-report.pdf or PROVISIONAL-cgt-report.txt.\n\nIncome and corporate-action exceptions are retained in period-events-outside-calculation.csv. The broker exclusion audit remains in data/cleanup-audit/excluded-rows.csv. Fund capital distributions (including VEXPX), ignored reinvested dividend credits and US/UK income classifications are not resolved by this report. FIG relief is not applied: the calculator summary is an ordinary CGT calculation and is not the tax payable under a FIG claim. Gains and losses are shown separately for that review.\n')
    print(json.dumps(figures,indent=2))


if __name__=='__main__':main()
