"""Catalog of standard line items and how to find them in SEC XBRL data.

This file is the single place that decides what "revenue", "capex", etc. mean.
Nothing in here is company-specific: every company goes through the same rules.

Each item:
  key        short name used everywhere (database, charts)
  label      human readable name
  statement  income / balance / cashflow
  kind       flow      = amount over a period, quarters add up to the year (revenue, capex)
             instant   = balance at a date (cash, debt)
             shares    = weighted-average share count over a period (not additive)
             eps       = per-share amount over a period, NOT additive (EPS)
             per_share = per-share amount over a period that IS additive (dividends/share)
             shares_instant = share count at a date
  tags       ordered candidates. First one that has a value for a given period wins.
             "A + B" means: sum of tags A and B (both must exist for that period).
             Tags without a prefix are us-gaap; use "dei:Tag" / "ifrs-full:Tag" otherwise.
  statement_label  (optional) regex: if a company uses its OWN tag on the item's statement
             (cash-flow statement or balance sheet) whose label matches, that tag is available as
             candidate "ext:<key>".
  formulas   fallbacks computed from other items when no tag has a value.
             Operands ending in "?" are optional (treated as 0 when missing),
             but at least one operand must exist.
             Items with no tags are always derived.
  bank       (optional) True = only kept for banks (see is_bank); banks get these lines instead
             of gross profit / operating income, which a bank does not have.
  insurer / reit  (optional) True = only kept for insurers / REITs (see INDUSTRY_SIC).
"""

# A company is treated as a bank when it reports customer deposits AND net interest income.
BANK_MARKERS = ("deposits", "net_interest_income")
# Insurers and REITs are recognised by their SEC industry code (SIC): 63xx = insurance carriers,
# 6798 = real estate investment trusts.
INDUSTRY_SIC = {"insurer": r"^63\d\d$", "reit": r"^6798$"}
# Lines that mean nothing for an insurer (no cost of goods, no "operating income" in the usual sense)
NOT_FOR_INSURERS = ("cost_of_revenue", "gross_profit", "operating_expenses", "operating_income", "ebitda")

ITEMS = [
    # ------------------------------------------------------------------ income statement
    dict(key="revenue", label="Revenue", statement="income", kind="flow",
         tags=["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet",
               "RevenueFromContractWithCustomerIncludingAssessedTax",
               "SalesRevenueGoodsNet + SalesRevenueServicesNet", "RevenuesNetOfInterestExpense",
               # before 2018 many product companies (Coca-Cola, J&J, Merck) tagged total revenue
               # only as "sales of goods"
               "SalesRevenueGoodsNet",
               "SalesRevenueServicesNet",   # service companies before 2018 (Equifax, Marsh)
               "SalesRevenueServicesGross",  # cruise lines (Carnival) before 2018
               "RealEstateRevenueNet",   # REITs: total revenues
               # industry-specific totals that older filings used instead of "Revenues"
               "RegulatedAndUnregulatedOperatingRevenue",   # utilities (Xcel, NextEra, DTE)
               "HealthCareOrganizationRevenue",             # hospitals, dialysis (DaVita)
               "RefiningAndMarketingRevenue",               # refiners (Valero)
               "FoodAndBeverageRevenue",                    # restaurants (Chipotle)
               "GasGatheringTransportationMarketingAndProcessingRevenue",   # midstream (Targa)
               "RevenuesExcludingInterestAndDividends",     # asset managers (BlackRock)
               "UtilityRevenue",                            # utilities (Entergy)
               "RevenueMineralSales",                       # miners (Newmont, Freeport)
               "OilAndGasRevenue",                          # oil and gas producers (EQT)
               "ext:revenue"],
         statement_label=r"^(total )?(net )?(revenues?|sales)( and other operating revenues?)?$"),
    # --- banks: revenue = net interest income + noninterest (fee) income
    dict(key="interest_income_total", label="Interest Income (total)", statement="income", kind="flow",
         bank=True, tags=["InterestAndDividendIncomeOperating", "InterestIncomeOperating"]),
    dict(key="interest_expense_total", label="Interest Expense (total)", statement="income", kind="flow",
         bank=True, tags=["InterestExpenseOperating", "InterestExpense"]),
    dict(key="net_interest_income", label="Net Interest Income", statement="income", kind="flow",
         bank=True, tags=["InterestIncomeExpenseNet", "InterestIncomeExpenseAfterProvisionForLoanLoss + ProvisionForLoanLeaseAndOtherLosses"],
         formulas=["interest_income_total - interest_expense_total"]),
    dict(key="noninterest_income", label="Noninterest (Fee) Income", statement="income", kind="flow",
         bank=True, tags=["NoninterestIncome"], formulas=["revenue - net_interest_income"]),
    dict(key="provision_credit_losses", label="Provision for Credit Losses", statement="income", kind="flow",
         bank=True, tags=["ProvisionForLoanLeaseAndOtherLosses", "ProvisionForCreditLosses",
                          "ProvisionForLoanAndLeaseLosses", "ProvisionForLoanLossesExpensed"]),
    dict(key="noninterest_expense", label="Noninterest Expense", statement="income", kind="flow",
         bank=True, tags=["NoninterestExpense"]),
    dict(key="pre_provision_profit", label="Pre-provision Profit", statement="income", kind="flow",
         bank=True, tags=[], formulas=["revenue - noninterest_expense"]),
    # --- insurers
    dict(key="premiums_earned", label="Premiums Earned (net)", statement="income", kind="flow", insurer=True,
         tags=["PremiumsEarnedNet", "PremiumsEarnedNetPropertyAndCasualty", "PremiumsEarnedNetLife"]),
    dict(key="claims_incurred", label="Losses & Claims Incurred", statement="income", kind="flow", insurer=True,
         tags=["PolicyholderBenefitsAndClaimsIncurredNet", "IncurredClaimsPropertyCasualtyAndLiability"]),
    dict(key="net_investment_income", label="Net Investment Income", statement="income", kind="flow",
         insurer=True, tags=["NetInvestmentIncome", "InvestmentIncomeNet"]),
    # Excise / sales taxes the company collects for the government. Some companies include them in
    # revenue (Philip Morris before 2018); then revenue is taken without them (see standardize 1e).
    dict(key="excise_taxes", label="Excise Taxes", statement="income", kind="flow",
         tags=["ExciseAndSalesTaxes"]),
    dict(key="cost_of_revenue", label="Cost of Revenue", statement="income", kind="flow",
         tags=["CostOfRevenue", "CostOfGoodsAndServicesSold",
               "CostOfGoodsSold + CostOfServices",   # goods and services tagged separately (RTX, GE)
               "CostOfGoodsSold",
               "CostOfGoodsAndServiceExcludingDepreciationDepletionAndAmortization"],
         formulas=["revenue - gross_profit"]),
    dict(key="gross_profit", label="Gross Profit", statement="income", kind="flow",
         tags=["GrossProfit"], formulas=["revenue - cost_of_revenue"]),
    dict(key="rnd", label="Research & Development", statement="income", kind="flow",
         tags=["ResearchAndDevelopmentExpense",
               "ResearchAndDevelopmentExpenseExcludingAcquiredInProcessCost"]),
    dict(key="sales_marketing", label="Sales & Marketing", statement="income", kind="flow",
         tags=["SellingAndMarketingExpense"]),
    dict(key="general_admin", label="General & Administrative", statement="income", kind="flow",
         tags=["GeneralAndAdministrativeExpense"]),
    dict(key="sga", label="SG&A", statement="income", kind="flow",
         tags=["SellingGeneralAndAdministrativeExpense"],
         formulas=["sales_marketing + general_admin"]),
    dict(key="operating_expenses", label="Operating Expenses", statement="income", kind="flow",
         tags=["OperatingExpenses"], formulas=["gross_profit - operating_income"]),
    # total of all costs, for companies that show "revenue - total costs" instead of an operating
    # income line (Exxon, GE)
    dict(key="costs_and_expenses", label="Total Costs & Expenses", statement="income", kind="flow",
         tags=["CostsAndExpenses"]),
    dict(key="operating_income", label="Operating Income", statement="income", kind="flow",
         tags=["OperatingIncomeLoss"],
         # no operating income line (Lilly, J&J, Merck, IBM, Exxon: about 70 of the S&P 500):
         # revenue - total costs, or pretax income with the non-operating items taken out again
         formulas=[# REITs without an operating line (Camden): profit + tax + interest, without property
                   # sale gains ("reit:" = only for REITs; their "costs and expenses" is often only the
                   # property costs, so this goes first)
                   "reit: net_income_incl_nci + income_tax? + interest_expense? - real_estate_gains?",
                   "revenue - costs_and_expenses",
                   "pretax_income + interest_expense? - interest_income? - other_nonop_other",
                   "pretax_income - other_nonoperating",
                   # last resort (TJX, Casey's, Clorox, Ball, Johnson Controls: no operating line and no
                   # "other" line): pretax income before interest, close to what they call operating profit
                   "nonfin: pretax_income + interest_expense? - interest_income?"]),
    dict(key="interest_expense", label="Interest Expense", statement="income", kind="flow",
         tags=["InterestExpense", "InterestExpenseNonoperating", "InterestExpenseDebt",
               "InterestAndDebtExpense", "InterestExpenseOperating"]),
    dict(key="interest_income", label="Interest Income", statement="income", kind="flow",
         tags=["InvestmentIncomeInterest", "InvestmentIncomeInterestAndDividend",
               "InterestIncomeOther", "InvestmentIncomeNonoperating"]),
    # Everything between operating income and pretax income (interest, investment gains, other).
    # One definition for everyone: the total, never a company's "other" sub-line.
    dict(key="other_nonoperating", label="Non-operating Income (net)", statement="income", kind="flow",
         tags=["NonoperatingIncomeExpense"], formulas=["pretax_income - operating_income"]),
    dict(key="other_nonop_other", label="Other Non-operating Income", statement="income", kind="flow",
         tags=["OtherNonoperatingIncomeExpense"]),
    dict(key="pretax_income", label="Pretax Income", statement="income", kind="flow",
         tags=["IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
               "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments"],
         # some companies only tag pretax income once a year: rebuild the quarters
         formulas=["net_income_incl_nci + income_tax - discontinued_ops? - extraordinary_items?",
                   "net_income + income_tax - discontinued_ops? - extraordinary_items?"]),
    dict(key="income_tax", label="Income Tax", statement="income", kind="flow",
         tags=["IncomeTaxExpenseBenefit"]),
    # Profit of businesses that were sold or shut down: part of net income, but reported
    # below the tax line (so net income = pretax income - tax + this).
    dict(key="discontinued_ops", label="Discontinued Operations (after tax)", statement="income", kind="flow",
         tags=["IncomeLossFromDiscontinuedOperationsNetOfTax",
               "IncomeLossFromDiscontinuedOperationsNetOfTaxAttributableToReportingEntity"]),
    # One-off gains/losses reported below the tax line under old accounting rules (abolished 2015),
    # e.g. JPMorgan's gain on buying Washington Mutual in 2008.
    dict(key="extraordinary_items", label="Extraordinary Items (after tax)", statement="income", kind="flow",
         tags=["ExtraordinaryItemNetOfTax", "ExtraordinaryItemsNetOfTaxAttributableToReportingEntity"]),
    # Share of profit of companies it owns a minority stake in. Some companies report it below the
    # tax line (pretax income tag ...BeforeIncomeTaxes...AndIncomeLossFromEquityMethodInvestments).
    dict(key="equity_method_income", label="Income from Equity Investments", statement="income", kind="flow",
         tags=["IncomeLossFromEquityMethodInvestments",
               "IncomeLossFromEquityMethodInvestmentsNetOfDividendsOrDistributions"]),
    dict(key="income_continuing_incl_nci", label="Income from Continuing Operations incl. Minority Interest",
         statement="income", kind="flow",
         tags=["IncomeLossFromContinuingOperationsIncludingPortionAttributableToNoncontrollingInterest"]),
    dict(key="net_income_incl_nci", label="Net Income incl. Minority Interest", statement="income",
         kind="flow", tags=["ProfitLoss",
                            "NetIncomeLossIncludingPortionAttributableToNonredeemableNoncontrollingInterest"],
         # companies that tag only the continuing-operations total (Accenture)
         formulas=["income_continuing_incl_nci + discontinued_ops?"]),
    dict(key="nci_income", label="Net Income to Minority Interest", statement="income", kind="flow",
         tags=["NetIncomeLossAttributableToNoncontrollingInterest",
               "NetIncomeLossAttributableToNonredeemableNoncontrollingInterest",
               "MinorityInterestInNetIncomeLossOfConsolidatedEntities",
               # the redeemable part alone is only part of it (Accenture): only together
               "NetIncomeLossAttributableToNonredeemableNoncontrollingInterest + NetIncomeLossAttributableToRedeemableNoncontrollingInterest"],
         # = profit incl. minority interest - profit for the shareholders, when not tagged separately
         formulas=["net_income_incl_nci - net_income"]),
    # Net income = profit for the company's shareholders (after minority interest, BEFORE preferred
    # dividends). One definition for everyone; never "available to common" or "incl. minority".
    dict(key="net_income", label="Net Income", statement="income", kind="flow",
         tags=["NetIncomeLoss"],
         formulas=["net_income_incl_nci - nci_income?", "net_income_common + preferred_dividends?"]),
    dict(key="preferred_dividends", label="Preferred Dividends", statement="income", kind="flow",
         tags=["PreferredStockDividendsIncomeStatementImpact", "DividendsPreferredStock",
               "PreferredStockDividendsAndOtherAdjustments"]),
    dict(key="net_income_common", label="Net Income to Common", statement="income", kind="flow",
         tags=["NetIncomeLossAvailableToCommonStockholdersBasic"],
         formulas=["net_income - preferred_dividends?"]),
    dict(key="shares_basic", label="Weighted Avg Shares (Basic)", statement="income", kind="shares",
         tags=["WeightedAverageNumberOfSharesOutstandingBasic"]),
    dict(key="shares_diluted", label="Weighted Avg Shares (Diluted)", statement="income", kind="shares",
         tags=["WeightedAverageNumberOfDilutedSharesOutstanding"],
         # no diluted count tagged (Exxon, Take-Two in loss years: nothing dilutes): the basic count
         formulas=["shares_basic"]),
    dict(key="eps_basic", label="EPS (Basic)", statement="income", kind="eps",
         tags=["EarningsPerShareBasic", "EarningsPerShareBasicAndDiluted"],
         formulas=["net_income_common / shares_basic"]),
    dict(key="eps_diluted", label="EPS (Diluted)", statement="income", kind="eps",
         tags=["EarningsPerShareDiluted", "EarningsPerShareBasicAndDiluted"],
         # no diluted figures at all for the period (no dilutive securities): diluted = basic
         formulas=["net_income_common / shares_diluted", "eps_basic"]),
    dict(key="dps", label="Dividends per Share (declared)", statement="income", kind="per_share",
         tags=["CommonStockDividendsPerShareDeclared", "CommonStockDividendsPerShareCashPaid"]),
    dict(key="ebitda", label="EBITDA", statement="income", kind="flow",
         # companies without an operating income line (Exxon, J&J, Lilly, IBM, ...): EBITDA
         # = profit before tax, interest, depreciation and amortization
         tags=[], formulas=["operating_income + d_and_a", "pretax_income + interest_expense? + d_and_a"]),
    # --- REITs: funds from operations = net income + real-estate depreciation - gains on property
    #     sales + real-estate impairments (NAREIT definition; total D&A is used for real-estate D&A).
    dict(key="real_estate_gains", label="Gains on Sale of Real Estate", statement="cashflow", kind="flow",
         reit=True, tags=["GainLossOnSaleOfProperties", "GainsLossesOnSalesOfInvestmentRealEstate",
                          "GainLossOnSaleOfRealEstateInvestmentPropertyNetOfApplicableIncomeTaxes",
                          "GainLossOnDispositionOfAssets", "GainLossOnDispositionOfAssets1", "ext:real_estate_gains"],
         # (gains on development properties are part of a developer's normal business: not removed)
         statement_label=r"^(net )?gains?( \(losses?\))?( on| from) (the )?(other )?(sales?|dispositions?) of "
                         r"(real estate|propert|investments? in real estate)"),
    dict(key="real_estate_impairment", label="Real Estate Impairments", statement="cashflow", kind="flow",
         reit=True, tags=["ImpairmentOfRealEstate"]),
    dict(key="ffo", label="Funds from Operations (FFO)", statement="income", kind="flow", reit=True,
         tags=["FundsFromOperations"],
         formulas=["net_income + d_and_a - real_estate_gains + real_estate_impairment?"]),
    dict(key="ffo_per_share", label="FFO per Share", statement="income", kind="eps", reit=True,
         tags=[], formulas=["ffo / shares_diluted"]),

    # ------------------------------------------------------------------ balance sheet
    dict(key="cash", label="Cash & Equivalents", statement="balance", kind="instant",
         # many companies with no restricted cash tag the balance-sheet line with the cash-flow total
         # (P&G, Chevron, 3M, GE since about 2018): used only when the plain tags are missing
         tags=["CashAndCashEquivalentsAtCarryingValue", "Cash", "CashAndDueFromBanks",
               "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
               "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalentsIncludingDisposalGroupAndDiscontinuedOperations"]),
    dict(key="short_term_investments", label="Short-term Investments", statement="balance", kind="instant",
         tags=["ShortTermInvestments", "MarketableSecuritiesCurrent",
               "AvailableForSaleSecuritiesDebtSecuritiesCurrent", "AvailableForSaleSecuritiesCurrent",
               "ext:short_term_investments", "DebtSecuritiesCurrent"],
         statement_label=r"^(short-term (investments|marketable securities)|marketable securities)(,? current)?$"),
    dict(key="cash_and_sti", label="Cash & Short-term Investments", statement="balance", kind="instant",
         tags=["CashCashEquivalentsAndShortTermInvestments"],
         formulas=["cash + short_term_investments?"]),
    dict(key="receivables", label="Accounts Receivable", statement="balance", kind="instant",
         tags=["AccountsReceivableNetCurrent", "ReceivablesNetCurrent"]),
    dict(key="inventory", label="Inventory", statement="balance", kind="instant",
         tags=["InventoryNet"]),
    dict(key="loans_gross", label="Loans (gross)", statement="balance", kind="instant", bank=True,
         tags=["LoansAndLeasesReceivableNetOfDeferredIncome",
               "FinancingReceivableExcludingAccruedInterestBeforeAllowanceForCreditLoss",
               "LoansAndLeasesReceivableGrossCarryingAmount"]),
    dict(key="loan_loss_allowance", label="Allowance for Loan Losses", statement="balance", kind="instant",
         bank=True, tags=["FinancingReceivableAllowanceForCreditLossExcludingAccruedInterest",
                          "FinancingReceivableAllowanceForCreditLosses", "LoansAndLeasesReceivableAllowance"]),
    dict(key="loans", label="Loans (net)", statement="balance", kind="instant", bank=True,
         tags=["LoansAndLeasesReceivableNetReportedAmount",
               "FinancingReceivableExcludingAccruedInterestAfterAllowanceForCreditLoss"],
         formulas=["loans_gross - loan_loss_allowance"]),
    dict(key="total_current_assets", label="Total Current Assets", statement="balance", kind="instant",
         tags=["AssetsCurrent"]),
    dict(key="long_term_investments", label="Long-term Investments", statement="balance", kind="instant",
         tags=["LongTermInvestments", "MarketableSecuritiesNoncurrent",
               "AvailableForSaleSecuritiesDebtSecuritiesNoncurrent", "AvailableForSaleSecuritiesNoncurrent"]),
    dict(key="ppe_net", label="PP&E (net)", statement="balance", kind="instant",
         tags=["PropertyPlantAndEquipmentNet",
               "PropertyPlantAndEquipmentAndFinanceLeaseRightOfUseAssetAfterAccumulatedDepreciationAndAmortization"]),
    dict(key="goodwill", label="Goodwill", statement="balance", kind="instant", tags=["Goodwill"]),
    dict(key="intangibles", label="Intangible Assets", statement="balance", kind="instant",
         tags=["IntangibleAssetsNetExcludingGoodwill", "FiniteLivedIntangibleAssetsNet",
               "OtherIntangibleAssetsNet"]),
    dict(key="total_assets", label="Total Assets", statement="balance", kind="instant", tags=["Assets"]),
    dict(key="deposits", label="Deposits", statement="balance", kind="instant", bank=True,
         tags=["Deposits"]),
    dict(key="accounts_payable", label="Accounts Payable", statement="balance", kind="instant",
         tags=["AccountsPayableCurrent"]),
    dict(key="total_current_liabilities", label="Total Current Liabilities", statement="balance",
         kind="instant", tags=["LiabilitiesCurrent"]),
    dict(key="current_portion_ltd", label="Current Portion of Long-term Debt", statement="balance",
         kind="instant", tags=["LongTermDebtCurrent", "LongTermDebtAndCapitalLeaseObligationsCurrent",
                               "DebtCurrent", "ConvertibleDebtCurrent", "ConvertibleNotesPayableCurrent"]),
    dict(key="commercial_paper", label="Commercial Paper / Short-term Borrowings", statement="balance",
         kind="instant", tags=["CommercialPaper", "ShortTermBorrowings", "LineOfCredit"]),
    # Oracle's 10-Qs give debt only as notes: "Notes payable, current" and "Notes and loans, noncurrent"
    # (its 10-Ks also give the usual DebtCurrent / combined total). Used only when nothing else is there.
    dict(key="notes_payable_current", label="Notes Payable (current)", statement="balance", kind="instant",
         tags=["NotesPayableCurrent"]),
    dict(key="notes_loans_noncurrent", label="Notes and Loans (non-current)", statement="balance", kind="instant",
         tags=["LongTermNotesAndLoans"]),
    dict(key="short_term_debt", label="Short-term Debt", statement="balance", kind="instant",
         tags=[], formulas=["current_portion_ltd? + commercial_paper?", "notes_payable_current"]),
    dict(key="long_term_debt_total", label="Long-term Debt incl. Current Portion", statement="balance",
         kind="instant", tags=["LongTermDebt", "LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities",
                               # REITs: unsecured notes (+ mortgages)
                               "NotesPayable + SecuredDebt", "NotesPayable"]),
    dict(key="long_term_debt", label="Long-term Debt", statement="balance", kind="instant",
         # convertible notes only (DoorDash, Datadog, Akamai, Palo Alto Networks)
         tags=["LongTermDebtNoncurrent", "LongTermDebtAndCapitalLeaseObligations",
               "ConvertibleLongTermNotesPayable", "ConvertibleDebtNoncurrent", "LongTermNotesPayable"],
         formulas=["long_term_debt_total - current_portion_ltd?", "notes_loans_noncurrent"]),
    dict(key="finance_lease_liabilities", label="Finance Lease Liabilities", statement="balance",
         kind="instant", tags=["FinanceLeaseLiability",
                               "FinanceLeaseLiabilityCurrent + FinanceLeaseLiabilityNoncurrent",
                               "CapitalLeaseObligations"]),
    # total debt as the company states it (some, e.g. insurers, give only this one number)
    dict(key="debt_stated_total", label="Debt (stated total)", statement="balance", kind="instant",
         tags=["DebtLongtermAndShorttermCombinedAmount"]),
    dict(key="borrowings", label="Borrowings (excl. leases)", statement="balance", kind="instant",
         tags=[], formulas=["short_term_debt? + long_term_debt", "debt_stated_total", "short_term_debt"]),
    # Total debt = borrowings + finance leases (operating leases excluded), same as most data vendors.
    dict(key="total_debt", label="Total Debt", statement="balance", kind="instant",
         tags=[], formulas=["borrowings? + finance_lease_liabilities?"]),
    dict(key="total_liabilities", label="Total Liabilities", statement="balance", kind="instant",
         tags=["Liabilities"], formulas=["liabilities_and_equity - total_equity_incl_nci - temporary_equity?"]),
    # "Mezzanine" equity between liabilities and equity: preferred shares before an IPO, or minority
    # stakes the company may have to buy back (e.g. Uber). Not a liability, not shareholders' equity.
    dict(key="temporary_equity", label="Redeemable (Mezzanine) Equity", statement="balance", kind="instant",
         tags=["TemporaryEquityCarryingAmountIncludingPortionAttributableToNoncontrollingInterests",
               "TemporaryEquityCarryingAmountAttributableToParent + RedeemableNoncontrollingInterestEquityCarryingAmount",
               "TemporaryEquityCarryingAmountAttributableToParent",
               "RedeemableNoncontrollingInterestEquityCarryingAmount",
               "RedeemableNoncontrollingInterestEquityCommonCarryingAmount",
               "TemporaryEquityCarryingAmount", "TemporaryEquityRedemptionValue",   # pre-2014 names
               "RedeemableNoncontrollingInterestEquityFairValue",
               "RedeemableNoncontrollingInterestEquityPreferredCarryingAmount",
               "RedeemableNoncontrollingInterestEquityRedemptionValue",
               "TemporaryEquityValueExcludingAdditionalPaidInCapital"]),   # e.g. convertible-debt conversion obligation
    dict(key="total_equity", label="Shareholders' Equity", statement="balance", kind="instant",
         tags=["StockholdersEquity"], formulas=["total_equity_incl_nci - noncontrolling_interest?"]),
    dict(key="noncontrolling_interest", label="Noncontrolling Interest", statement="balance",
         kind="instant", tags=["MinorityInterest", "NonredeemableNoncontrollingInterest"]),
    dict(key="total_equity_incl_nci", label="Total Equity incl. NCI", statement="balance", kind="instant",
         tags=["StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
         formulas=["total_equity + noncontrolling_interest?"]),
    dict(key="liabilities_and_equity", label="Total Liabilities & Equity", statement="balance",
         kind="instant", tags=["LiabilitiesAndStockholdersEquity"]),
    dict(key="retained_earnings", label="Retained Earnings", statement="balance", kind="instant",
         tags=["RetainedEarningsAccumulatedDeficit"]),
    dict(key="shares_outstanding", label="Shares Outstanding", statement="balance", kind="shares_instant",
         tags=["CommonStockSharesOutstanding", "dei:EntityCommonStockSharesOutstanding"]),

    # ------------------------------------------------------------------ cash flow statement
    dict(key="d_and_a", label="Depreciation & Amortization", statement="cashflow", kind="flow",
         # ext:d_and_a_sum = sum of all D&A lines on the cash-flow statement (only exists when
         # the company splits D&A over several lines; see filings.py)
         tags=["ext:d_and_a_sum", "DepreciationDepletionAndAmortization", "DepreciationAmortizationAndAccretionNet",
               "DepreciationAndAmortization", "ext:d_and_a",
               "Depreciation + AmortizationOfIntangibleAssets",
               "Depreciation"],   # company shows only depreciation (no separate amortization line)
         statement_label=r"^depreciation(,| and)? (depletion,? )?(and )?amortization"),
    dict(key="sbc", label="Stock-based Compensation", statement="cashflow", kind="flow",
         tags=["ShareBasedCompensation", "AllocatedShareBasedCompensationExpense", "ext:sbc"],
         statement_label=r"^(stock|share)[- ]based compensation"),
    dict(key="operating_cf", label="Operating Cash Flow", statement="cashflow", kind="flow",
         tags=["NetCashProvidedByUsedInOperatingActivities",
               "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"]),
    # --- REITs: capex = upkeep of the buildings they own (capital improvements, tenant improvements and
    #     leasing costs), not building or buying new properties (that is growth, like an acquisition).
    #     Used for capex whenever a REIT reports one of these lines (standardize.py, step 1y).
    dict(key="capital_improvements", label="Capital Improvements", statement="cashflow", kind="flow",
         reit=True, tags=["PaymentsForCapitalImprovements"]),
    dict(key="tenant_improvements", label="Tenant Improvements & Leasing Costs", statement="cashflow",
         kind="flow", reit=True,
         tags=["PaymentsForLeasingCostsCommissionsAndTenantImprovements", "PaymentsForTenantImprovements"]),
    dict(key="capex", label="Capital Expenditures", statement="cashflow", kind="flow",
         tags=["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets",
               "PaymentsToAcquireOtherPropertyPlantAndEquipment",   # Lilly
               "PaymentsToAcquireOtherProductiveAssets",            # Verizon ("capital expenditures, incl. software")
               "PaymentsForCapitalImprovements",                    # Corning, Gartner, Snap-on ("capital expenditures")
               "PaymentsForConstructionInProcess",                  # Con Ed ("utility capital expenditures")
               "ext:capex"],
         # company-specific capex lines: "Capital expenditures and investments" (Phillips 66, ConocoPhillips),
         # "Purchases of rental equipment" (United Rentals), "Plant construction and other property additions"
         # (Dominion). Not "Capital expenditures of <one subsidiary>" (NextEra): that is only part of it.
         statement_label=r"^(additions to|purchases? of|payments for( acquisition of)?) property"
                         r"|^(total )?capital expenditures(?! of)(?!.*(accru|incurred|payable|unpaid))"
                         r"|^(utility )?construction expenditures|^(payments for )?purchases? of rental equipment"
                         r"|^plant construction|^(plant|property) additions"),
    dict(key="fcf", label="Free Cash Flow", statement="cashflow", kind="flow",
         tags=[], formulas=["operating_cf - capex"]),
    dict(key="acquisitions", label="Acquisitions", statement="cashflow", kind="flow",
         tags=["PaymentsToAcquireBusinessesNetOfCashAcquired", "PaymentsToAcquireBusinessesGross",
               "ext:acquisitions"],
         statement_label=r"^(acquisitions?|purchases?) of (companies|businesses)|^acquisitions?,? net of cash"),
    dict(key="investing_cf", label="Investing Cash Flow", statement="cashflow", kind="flow",
         tags=["NetCashProvidedByUsedInInvestingActivities",
               "NetCashProvidedByUsedInInvestingActivitiesContinuingOperations"]),
    dict(key="dividends_paid", label="Dividends Paid", statement="cashflow", kind="flow",
         tags=["PaymentsOfDividends", "PaymentsOfDividendsCommonStock", "ext:dividends_paid"],
         statement_label=r"^(common stock )?(cash )?dividends paid|^payments? (for|of) dividends"),
    dict(key="buybacks", label="Share Buybacks", statement="cashflow", kind="flow",
         tags=["PaymentsForRepurchaseOfCommonStock", "ext:buybacks"],
         statement_label=r"^(common stock repurchased|repurchases? of common stock)"),
    dict(key="stock_issued", label="Stock Issued", statement="cashflow", kind="flow",
         tags=["ProceedsFromIssuanceOfCommonStock"]),
    dict(key="debt_issued", label="Debt Issued", statement="cashflow", kind="flow",
         tags=["ProceedsFromIssuanceOfLongTermDebt", "ProceedsFromIssuanceOfDebt",
               "ProceedsFromIssuanceOfLongTermDebtAndCapitalSecuritiesNet",
               "ProceedsFromDebtMaturingInMoreThanThreeMonths",
               "ProceedsFromIssuanceOfSeniorLongTermDebt"]),
    dict(key="debt_repaid", label="Debt Repaid", statement="cashflow", kind="flow",
         tags=["RepaymentsOfLongTermDebt", "RepaymentsOfDebt", "RepaymentsOfLongTermDebtAndCapitalSecurities",
               "RepaymentsOfDebtMaturingInMoreThanThreeMonths", "RepaymentsOfSeniorDebt"]),
    dict(key="short_term_debt_net", label="Short-term Debt Issued/Repaid (net)", statement="cashflow",
         kind="flow", tags=["ProceedsFromRepaymentsOfShortTermDebt", "ProceedsFromRepaymentsOfCommercialPaper",
                            "ProceedsFromRepaymentsOfShortTermDebtMaturingInThreeMonthsOrLess"]),  # totals first
    dict(key="financing_cf", label="Financing Cash Flow", statement="cashflow", kind="flow",
         tags=["NetCashProvidedByUsedInFinancingActivities",
               "NetCashProvidedByUsedInFinancingActivitiesContinuingOperations"]),
]

ITEM_BY_KEY = {i["key"]: i for i in ITEMS}
for _i in ITEMS:
    _i.setdefault("formulas", [])
    _i.setdefault("bank", False)
    _i.setdefault("insurer", False)
    _i.setdefault("reit", False)
