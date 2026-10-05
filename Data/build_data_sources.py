# Regenerates investor_dashboard_data_sources.xlsx (Investor Dashboard data source catalog).
# Requires: pip install openpyxl holidays exchange_calendars
# Run: python build_data_sources.py, then recalculate formulas (LibreOffice/Excel) so cached totals appear.
# Edit the rows / S (schedule) / PRI / DECLINED / IMPACT tables below, then rerun.
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.table import Table, TableStyleInfo

F="Arial"
rows = [
# Category, Data item, Source, Type, Endpoint/URL, Example params, Auth, Cost, Refresh, Tier, Format, Role, Notes
("Stock price","Official daily close for TSLA, SPCX + ETF proxies (verifies Alpaca)","Massive (formerly Polygon.io)","REST","https://api.massive.com/v2/aggs/ticker/{TICKER}/prev","One call per ticker after the close (9 tickers); throttle to 5 calls/min (~2 minutes per run)","API key","Free Basic tier (5 calls/min, end-of-day + 15-min delayed)","Hourly (market hours, ~7 polls/day/ticker) + EOD close","T1 Hourly","JSON","Backup","Polygon rebranded to Massive in 2025. Free tier only; also the failover for hourly bars if Alpaca is down"),
("Stock price","Hourly bars: TSLA, SPCX + index/sector ETF proxies (SPY, DIA, QQQ, IWM, XLY, ITA, SMH) + any user-added tickers (primary)","Alpaca Market Data","REST","https://data.alpaca.markets/v2/stocks/bars?symbols=TSLA,SPCX,SPY,DIA,QQQ,IWM,XLY,ITA,SMH&timeframe=1Hour&feed=iex","Both tickers in one call; snapshots: /v2/stocks/snapshots?symbols=TSLA,SPCX","API key + secret (free account)","Free Basic plan (IEX feed, 200 calls/min)","","","JSON","Primary","One call covers both tickers (~7 calls/day). Prices are accurate; volume is IEX-only (a small share of total) – use for trend, not absolute volume"),
("Stock price","Latest quote; daily series","Alpha Vantage","REST","https://www.alphavantage.co/query?function=GLOBAL_QUOTE&symbol=TSLA&apikey={KEY}","Hourly GLOBAL_QUOTE for TSLA+SPCX ≈ 14 calls/day; TIME_SERIES_DAILY for EOD","API key","Free 25 req/day; paid tiers","Hourly (market hours, ~7 polls/day/ticker) + EOD close","T1 Hourly","JSON/CSV","Backup","Hourly polling of 2 tickers now fits the free 25/day limit (leaves ~11 for news)"),
("Stock price","Quote","Finnhub","REST","https://finnhub.io/api/v1/quote?symbol=TSLA&token={KEY}","One call per ticker per hour","API key","Free 60 req/min","Hourly (market hours, ~7 polls/day/ticker) + EOD close","T1 Hourly","JSON","Backup","Same key also covers news + calendars; WebSocket not needed"),
("Stock price","5-year daily history backfill per ticker","Yahoo Finance","REST (unofficial)","https://query1.finance.yahoo.com/v8/finance/chart/{TICKER}?interval=1d&range=5y","Python: yfinance; one call per ticker at setup (9 tickers) and again whenever a user adds a ticker","None","Free","Hourly / daily backfill","T1 Hourly","JSON","Fallback","Unofficial, no SLA, may throttle. Used only for one-time backfill (not polled); adjusted closes – store split/dividend-adjusted and raw"),
("SpaceX valuation / listing","SPCX listing details, share count, lock-up, filings","SEC EDGAR (company_tickers + submissions)","REST","https://www.sec.gov/files/company_tickers.json  ->  https://data.sec.gov/submissions/CIK{10-digit CIK}.json","Resolve SPCX CIK from company_tickers.json; S-1/424B4 prospectus, Form 4 insider trades, 13D/G","None (User-Agent header with email required)","Free","15 min (≤10 req/s)","T1 Real-time","JSON","Primary","SpaceX listed on Nasdaq as SPCX on 12 Jun 2026; track lock-up expiry dates from prospectus"),
("SpaceX valuation / listing","Pre-IPO tender/secondary price history (historical only)","Forge Global / Nasdaq Private Market","Web","https://forgeglobal.com/spacex_stock/ ; https://www.nasdaqprivatemarket.com/company/spacex/","One-time backfill of pre-IPO valuations","Account for detail","Free summary","One-time","T4 Event","HTML","Reference","Only needed for pre-June-2026 history"),
("SpaceX operations","Launch schedule & outcomes (Starship, Falcon, Starlink)","The Space Devs – Launch Library 2","REST","https://ll.thespacedevs.com/2.3.0/launches/upcoming/?lsp__name=SpaceX","Also /launches/previous/; dev mirror lldev.thespacedevs.com for testing","None (key for higher limits)","Free 15 req/hr; Patreon tiers","Hourly","T2 Daily","JSON","Primary","Launch failures/successes are company-specific price catalysts"),
("SpaceX operations","Company updates","SpaceX website","Web","https://www.spacex.com/updates/","Scrape/monitor for new posts","None","Free","Hourly","T4 Event","HTML","Secondary",""),
("Tesla fundamentals","Filings: 10-Q, 10-K, 8-K, Form 4","SEC EDGAR","REST + Atom","https://data.sec.gov/submissions/CIK0001318605.json","Atom: https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=0001318605&type=8-K&output=atom","None (User-Agent header required)","Free","15 min","T1 Real-time","JSON / Atom XML","Primary","Tesla CIK = 0001318605"),
("Tesla fundamentals","Structured financials incl. gross margin (GrossProfit ÷ Revenues), EPS","SEC EDGAR XBRL Company Facts","REST","https://data.sec.gov/api/xbrl/companyfacts/CIK0001318605.json","Concepts us-gaap:GrossProfit and us-gaap:Revenues (gross margin = ratio, per quarter); single concept: /api/xbrl/companyconcept/CIK0001318605/us-gaap/GrossProfit.json","None (User-Agent header)","Free","Quarterly","T3 Scheduled","JSON","Primary","Gross margin computed per fiscal quarter from 10-Q/10-K facts (Q4 = FY − 9M when only annual is reported)"),
("Tesla fundamentals","Quarterly deliveries & production; shareholder deck incl. FSD active subscribers (where disclosed)","Tesla Investor Relations","Web","https://ir.tesla.com/","Deliveries press release ~2nd day after quarter end; shareholder deck with earnings","None","Free","Quarterly","T3 Scheduled","HTML/PDF","Primary","UI plots deliveries on their release date (~2nd day after quarter end), not quarter end. FSD active subscribers: quarterly, only where Tesla discloses it; may need manual entry if the deck is not structured"),
("Earnings calendar","TSLA & SPCX earnings dates, EPS estimates","Finnhub","REST","https://finnhub.io/api/v1/calendar/earnings?symbol=TSLA&from={d}&to={d}&token={KEY}","","API key","Free","Daily","T2 Daily","JSON","Primary","Alt: Alpha Vantage function=EARNINGS_CALENDAR"),
("Bond yields","Daily Treasury par yield curve (1M–30Y)","US Treasury","XML / CSV","https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml?data=daily_treasury_yield_curve&field_tdr_date_value=2026","CSV: .../daily-treasury-rates.csv/2026/all?type=daily_treasury_yield_curve","None","Free","Daily (~3:30pm ET)","T2 Daily","XML/CSV","Primary","Authoritative source for curve"),
("Bond yields","2Y, 10Y, 30Y yields; 10Y–2Y spread; real yields","FRED (St. Louis Fed)","REST","https://api.stlouisfed.org/fred/series/observations?series_id=DGS10&api_key={KEY}&file_type=json","series_id = DGS2, DGS10, DGS30, T10Y2Y, DFII10 (see Series IDs tab)","API key (free)","Free","Daily (1-day lag)","T2 Daily","JSON","Primary","Simplest single API for all macro series"),
("Interest rates","Fed funds target range (upper/lower), effective rate","FRED","REST","https://api.stlouisfed.org/fred/series/observations?series_id=DFEDTARU&api_key={KEY}&file_type=json","DFEDTARU, DFEDTARL, EFFR","API key","Free","Daily","T2 Daily","JSON","Primary",""),
("Interest rates","Effective Fed Funds Rate & SOFR","NY Fed Markets API","REST","https://markets.newyorkfed.org/api/rates/unsecured/effr/last/1.json","SOFR: /api/rates/secured/sofr/last/1.json","None","Free","Daily (~9am ET)","T2 Daily","JSON","Primary",""),
("Interest rates","FOMC statements, minutes, press releases","Federal Reserve","RSS","https://www.federalreserve.gov/feeds/press_monetary.xml","All releases: /feeds/press_all.xml; Speeches: /feeds/speeches.xml","None","Free","Every 15 min on FOMC days; hourly otherwise","T3 Scheduled","RSS XML","Primary","Decision released 2:00pm ET on meeting day"),
("Interest rates","FOMC meeting calendar & dot plot (SEP)","Federal Reserve","Web","https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm","SEP published at Mar/Jun/Sep/Dec meetings","None","Free","Monthly check","T3 Scheduled","HTML","Primary","8 scheduled meetings/year"),
("Interest rates","Market-implied rate-change probabilities","CME FedWatch","Web / paid API","https://www.cmegroup.com/markets/interest-rates/cme-fedwatch-tool.html","API via CME DataMine / FedWatch API subscription","Subscription for API","Paid API; web free","Daily","T2 Daily","HTML/JSON","Primary","Free alternative: derive from 30-day Fed funds futures"),
("Inflation","CPI-U headline & core (SA/NSA)","BLS Public Data API v2","REST (POST)","https://api.bls.gov/publicAPI/v2/timeseries/data/","Body: {\"seriesid\":[\"CUSR0000SA0\",\"CUSR0000SA0L1E\"],\"startyear\":\"2024\",\"endyear\":\"2026\",\"registrationkey\":\"{KEY}\"}","Registration key (free)","Free (500 req/day w/ key)","Monthly (8:30am ET release)","T3 Scheduled","JSON","Primary","Authoritative source"),
("Inflation","CPI release schedule","BLS","Web / ICS","https://www.bls.gov/schedule/news_release/cpi.htm","","None","Free","Annually + monthly check","T3 Scheduled","HTML","Primary","Drives when the CPI agent wakes up"),
("Inflation","CPI, core CPI, PCE, core PCE, breakeven inflation","FRED","REST","https://api.stlouisfed.org/fred/series/observations?series_id=CPIAUCSL&api_key={KEY}&file_type=json","CPIAUCSL, CPILFESL, PCEPI, PCEPILFE, T10YIE","API key","Free","Monthly / daily (T10YIE)","T3 Scheduled","JSON","Backup","FRED release dates: /fred/release/dates?release_id=10 (CPI)"),
("Inflation","PCE price index (Fed's preferred gauge)","BEA API","REST","https://apps.bea.gov/api/data?UserID={KEY}&method=GetData&DataSetName=NIPA&TableName=T20804&Frequency=M&Year=2026","","API key (free)","Free","Monthly","T3 Scheduled","JSON","Optional","Not in original scope but moves rate expectations"),
("Economic calendar","Upcoming macro releases w/ consensus vs actual","Finnhub","REST","https://finnhub.io/api/v1/calendar/economic?token={KEY}","","API key","Paid (premium endpoint)","Daily","T2 Daily","JSON","Primary","Needed to compute CPI 'surprise' (actual – consensus)"),
("Economic calendar","Release dates for any FRED series","FRED Releases API","REST","https://api.stlouisfed.org/fred/releases/dates?api_key={KEY}&file_type=json&realtime_start={today}","","API key","Free","Daily","T2 Daily","JSON","Backup","Dates only — no consensus forecasts"),
("Tariffs","Tariff rules, Section 301/232 notices, exclusions","Federal Register API","REST","https://www.federalregister.gov/api/v1/documents.json?conditions[term]=tariff&conditions[agencies][]=trade-representative-office-of-united-states&order=newest","Also agencies: international-trade-administration, industry-and-security-bureau, customs-and-border-protection","None","Free","Hourly (published daily ~6am ET)","T4 Event","JSON","Primary","Most structured source for legal tariff actions"),
("Tariffs","Presidential tariff proclamations & executive orders","White House","Web","https://www.whitehouse.gov/presidential-actions/","Monitor for 'tariff', 'Section 232', 'IEEPA'","None","Free","Hourly","T4 Event","HTML","Primary","Often appears before Federal Register publication"),
("Tariffs","USTR press releases (trade deals, Section 301)","USTR","Web","https://ustr.gov/about-us/policy-offices/press-office/press-releases","","None","Free","Hourly","T4 Event","HTML","Primary",""),
("Tariffs","Current tariff rates by HTS code (autos 8703, batteries 8507)","USITC HTS REST API","REST","https://hts.usitc.gov/reststop/search?keyword=8703","Also /reststop/exportList for full schedule","None","Free","Weekly","T4 Event","JSON","Primary","Reference table for affected product codes"),
("Tariffs","CBP implementation notices (CSMS)","US Customs & Border Protection","Web / email","https://www.cbp.gov/trade/automated/cargo-systems-messaging-service","","None","Free","Daily","T4 Event","HTML","Secondary","Effective-date details for collection"),
("Tariffs","Export controls (chips, batteries, critical minerals)","Commerce – BIS","Web","https://www.bis.gov/news-updates","Also via Federal Register agency=industry-and-security-bureau","None","Free","Daily","T4 Event","HTML","Secondary",""),
("Tariffs","China counter-measures (rare earths, export bans)","China MOFCOM","Web","http://english.mofcom.gov.cn/","","None","Free","Daily","T4 Event","HTML","Secondary","English site lags Chinese site"),
("Tariffs","US import/export volumes by HS code","Census International Trade API","REST","https://api.census.gov/data/timeseries/intltrade/imports/hs?get=GEN_VAL_MO,CTY_NAME&I_COMMODITY=8703&time=2026-07","Exports: /intltrade/exports/hs","API key (optional)","Free","Monthly","T3 Scheduled","JSON","Optional","Measures real trade impact"),
("Geopolitical","Global news events, tone, themes","GDELT DOC 2.0 API","REST","https://api.gdeltproject.org/api/v2/doc/doc?query=(tariff OR sanctions OR Taiwan) sourcelang:english&mode=artlist&format=json&timespan=1h","mode=timelinetone for sentiment trend","None","Free","15 min","T1 Real-time","JSON","Primary","Updates every 15 min; noisy, needs filtering"),
("Geopolitical","Conflict & protest events (geo-coded)","ACLED API","REST (OAuth)","https://acleddata.com/api/acled/read?_format=json&event_date={d}","Token: POST https://acleddata.com/oauth/token (myACLED account)","OAuth (myACLED account)","Free for non-commercial / research tier","Weekly","T4 Event","JSON","Secondary","Commercial use requires license"),
("Geopolitical","US sanctions actions","Treasury OFAC","Web / data files","https://ofac.treasury.gov/recent-actions","SDN list download: https://sanctionslist.ofac.treas.gov/","None","Free","Daily","T4 Event","HTML / XML","Primary",""),
("Geopolitical","Geopolitical Risk (GPR) Index","Caldara & Iacoviello (Fed Board)","Download","https://www.matteoiacoviello.com/gpr.htm","Daily & monthly XLS files","None","Free","Weekly","T2 Daily","XLS","Primary","Single numeric risk gauge for charts"),
("Geopolitical","Economic Policy Uncertainty index (daily)","FRED","REST","https://api.stlouisfed.org/fred/series/observations?series_id=USEPUINDXD&api_key={KEY}&file_type=json","","API key","Free","Daily","T2 Daily","JSON","Secondary",""),
("Financial news","Ticker news + sentiment (union of all users' tickers, rotated)","Alpha Vantage NEWS_SENTIMENT","REST","https://www.alphavantage.co/query?function=NEWS_SENTIMENT&tickers={TICKER}&apikey={KEY}","One ticker per call (multiple tickers narrow results to articles mentioning all of them); rotation e.g. TSLA, SPCX, TSLA, SPCX… then user-added tickers","API key","Free 25 req/day","Hourly","T1 Real-time","JSON","Primary","Pre-scored sentiment per ticker"),
("Financial news","Company news","Finnhub","REST","https://finnhub.io/api/v1/company-news?symbol=TSLA&from={d}&to={d}&token={KEY}","General: /news?category=general","API key","Free","15 min","T1 Real-time","JSON","Primary",""),
("Financial news","Ticker news","Massive (Polygon) News","REST","https://api.massive.com/v2/reference/news?ticker=TSLA&limit=50","Includes per-ticker sentiment insights","API key","Included in plan","15 min","T1 Real-time","JSON","Backup",""),
("Financial news","Broad headline search","NewsAPI","REST","https://newsapi.org/v2/everything?q=Tesla OR SpaceX&sortBy=publishedAt&apiKey={KEY}","","API key","Free dev (24h delay); paid prod","15 min","T1 Real-time","JSON","Backup","Free tier not licensed for production"),
("Financial news","Headline RSS","Google News RSS","RSS","https://news.google.com/rss/search?q=Tesla+stock&hl=en-US&gl=US&ceid=US:en","Separate feeds for SpaceX, Fed, tariffs","None","Free","15 min","T1 Real-time","RSS XML","Fallback","No sentiment; aggregator links"),
("Market context","VIX, US Dollar Index, WTI oil","FRED","REST","https://api.stlouisfed.org/fred/series/observations?series_id=VIXCLS&api_key={KEY}&file_type=json","VIXCLS, DTWEXBGS, DCOILWTICO","API key","Free","Daily","T2 Daily","JSON","Optional","Risk-sentiment overlays"),
]
NEW_START = len(rows)
rows += [
# ---------- Robotaxi fleet: US federal ----------
("Robotaxi – US federal","Crash reports for driverless (ADS) vehicles incl. Tesla Robotaxi/Cybercab","NHTSA Standing General Order 2021-01","CSV download","https://static.nhtsa.gov/odi/ffdd/sgo-2021-01/SGO-2021-01_Incident_Reports_ADS.csv","Filter Reporting Entity = Tesla; L2 file: .../SGO-2021-01_Incident_Reports_ADAS.csv (FSD Supervised)","None","Free","Monthly","T3 Scheduled","CSV","Primary","Archive files for pre-16-Jun-2025 data on https://www.nhtsa.gov/SGOCrashReporting"),
("Robotaxi – US federal","Defect investigations, audit queries, special orders (e.g., Cybercab AQ26002)","NHTSA Office of Defects Investigation","CSV (zip) + Web","https://static.nhtsa.gov/odi/ffdd/inv/FLAT_INV.zip","Search UI: https://www.nhtsa.gov/recalls (make=TESLA); press: https://www.nhtsa.gov/press-releases","None","Free","Daily","T4 Event","TXT/CSV","Primary","Investigation openings/escalations are price-moving"),
("Robotaxi – US federal","Recalls & complaints for Tesla models","NHTSA vPIC / Recalls API","REST","https://api.nhtsa.gov/recalls/recallsByVehicle?make=TESLA&model=MODEL%20Y&modelYear=2026","Complaints: https://api.nhtsa.gov/complaints/complaintsByVehicle?make=TESLA&model=CYBERCAB&modelYear=2026","None","Free","Daily","T2 Daily","JSON","Primary",""),
("Robotaxi – US federal","FMVSS exemptions (Cybercab has no steering wheel/pedals)","NHTSA via Federal Register API","REST","https://www.federalregister.gov/api/v1/documents.json?conditions[term]=Tesla exemption&conditions[agencies][]=national-highway-traffic-safety-administration","Also AV framework rulemakings (AV STEP)","None","Free","Daily","T4 Event","JSON","Primary","Exemption cap limits Cybercab volume"),
# ---------- Robotaxi: states ----------
("Robotaxi – California","AV testing / driverless / deployment permit holders","California DMV","Web","https://www.dmv.ca.gov/portal/vehicle-industry-services/autonomous-vehicles/autonomous-vehicle-testing-permit-holders/","Watch for Tesla moving from 'testing with driver' to driverless or deployment permit","None","Free","Weekly","T4 Event","HTML","Primary","Tesla currently holds testing-with-driver permit only"),
("Robotaxi – California","AV collision reports (OL 316)","California DMV","Web (PDF per report)","https://www.dmv.ca.gov/portal/vehicle-industry-services/autonomous-vehicles/autonomous-vehicle-collision-reports/","Structured mirror: https://tims.berkeley.edu/tools/avsafety.php","None","Free","Weekly","T4 Event","PDF/HTML","Primary",""),
("Robotaxi – California","Annual disengagement & mileage reports","California DMV","Web (XLSX/CSV)","https://www.dmv.ca.gov/portal/vehicle-industry-services/autonomous-vehicles/disengagement-reports/","Reporting period Dec 1–Nov 30; published early each year","None","Free","Annual","T3 Scheduled","XLSX/CSV","Primary",""),
("Robotaxi – California","Passenger-carrying permits (Tesla TCP0046782-A) & AV quarterly data","California Public Utilities Commission (CPUC)","Web","https://www.cpuc.ca.gov/regulatory-services/licensing/transportation-licensing-and-analysis-branch/autonomous-vehicle-programs","Quarterly AV program data reports (trips, miles, incidents) for deployment permit holders","None","Free","Quarterly","T3 Scheduled","XLSX/PDF","Primary","Bay Area service runs under charter-party (limo) permit, not AV deployment"),
("Robotaxi – Texas","Commercial AV authorizations & AV-registered vehicles (fleet count by model)","Texas DMV – Automated Motor Vehicles Program","Web","https://www.txdmv.gov/AVprogram","Authorization enforceable since 28 May 2026; vehicle verification portal linked from page","None","Free","Weekly","T4 Event","HTML","Primary","Best official proxy for Austin/Dallas/Houston fleet size (476 Tesla vehicles as of 21 Sep 2026)"),
("Robotaxi – Nevada","Autonomous Vehicle Network Company permits (Tesla AVNC Permit 002, up to 5,000 vehicles)","Nevada Transportation Authority (Dept. of Business & Industry)","Web","https://www.business.nv.gov/news-media/press-releases/","Also Nevada DMV AV testing licenses: https://dmv.nv.gov/autonomous.htm","None","Free","Weekly","T4 Event","HTML","Primary","Approved 21 Aug 2026; Las Vegas launch pending"),
("Robotaxi – Arizona","AV self-certification & TNC (ride-hail) permits","Arizona DOT (ADOT)","Web","https://azdot.gov/","Search ADOT site for 'autonomous vehicle'; Tesla got TNC permit Nov 2025","None","Free","Weekly","T4 Event","HTML","Secondary","Phoenix launch preparations per Q2 2026 disclosure"),
("Robotaxi – Florida","No state AV permit required; track via city/airport approvals & news","Florida HSMV / local news","Web","https://www.flhsmv.gov/","Use news agents for Miami, Orlando, Tampa service-area changes","None","Free","Weekly","T4 Event","HTML","Secondary","Fewest official data points of any operating state"),
# ---------- Robotaxi: international ----------
("Robotaxi – Europe","FSD (Supervised) type approval & national recognitions","RDW (Netherlands vehicle authority)","Web","https://www.rdw.nl/","EU harmonisation via TCMV; DCAS rule UN R171 at https://unece.org/transport/vehicle-regulations","None","Free","Weekly","T4 Event","HTML","Primary","8 countries approved as of 29 Sep 2026 (NL, BE, CZ, DK, HR, LT, SI, EE)"),
("Robotaxi – China","FSD / 'Tesla Assisted Driving' approval, L3/L4 pilot permits","MIIT (Ministry of Industry & IT)","Web","https://www.miit.gov.cn/","Product announcements list; also Shanghai/Beijing robotaxi pilot zone notices","None","Free","Weekly","T4 Event","HTML (Chinese)","Secondary","Supervised FSD live since 21 May 2026; full approval pending"),
# ---------- Robotaxi: company + trackers ----------
("Robotaxi – company","Service areas, cities, app availability","Tesla Robotaxi site / X account","Web","https://www.tesla.com/robotaxi","Also official X: @robotaxi and @Tesla_AI for area expansions","None","Free","Daily","T4 Event","HTML","Primary","Tesla doesn't publish fleet size; quarterly letters give cumulative miles"),
("Robotaxi – trackers","Aggregated state permit rosters & fleet sizes (Tesla, Waymo, Zoox)","Robotaxi Tracker","Web","https://robotaxitracker.com/permits","Methodology: https://robotaxitracker.com/methodology","None","Free (attribution required)","Daily","T2 Daily","HTML","Secondary","Unofficial – cross-check against TxDMV/CA DMV"),
("Robotaxi – trackers","Robotaxi status by city/company","The Charge Port Robotaxi Tracker","Web","https://thechargeport.com/robotaxi-tracker","","None","Free","Weekly","T2 Daily","HTML","Secondary","Unofficial"),
("Robotaxi – trackers","FSD approvals by country (EU) & US rollout","Not a Tesla App / NotAnFSDTracker","Web","https://notanfsdtracker.com/eu-tracker","US: https://notanfsdtracker.com/united-states-tesla-robotaxi-rollout-2026","None","Free","Weekly","T2 Daily","HTML","Secondary","Unofficial"),
# ---------- SpaceX launches ----------
("SpaceX launches","FAA launch/reentry licenses, Starship license mods, mishap investigations","FAA Office of Commercial Space Transportation","Web","https://www.faa.gov/space/licenses/operator_licenses_permits","Data: https://www.faa.gov/data_research/commercial_space_data ; Starship: https://www.faa.gov/space/stakeholder_engagement/spacex_starship","None","Free","Daily","T4 Event","HTML","Primary","Groundings after mishaps move SPCX"),
("SpaceX launches","Starlink constellation size (on-orbit satellites)","CelesTrak","REST","https://celestrak.org/NORAD/elements/gp.php?GROUP=starlink&FORMAT=json","Count objects per day for constellation growth","None","Free","Daily","T2 Daily","JSON","Primary","Space-Track.org is the authoritative alt (free account)"),
("SpaceX launches","Launch & Starlink statistics","Jonathan McDowell – planet4589","Web","https://planet4589.org/space/con/star/stats.html","","None","Free","Weekly","T2 Daily","HTML","Secondary","Widely used independent stats"),
# ---------- SpaceX customer orders / contracts ----------
("SpaceX contracts","All federal awards to SpaceX (NASA, Space Force, NRO, etc.)","USAspending.gov API","REST (POST)","https://api.usaspending.gov/api/v2/search/spending_by_award/","Body filters: recipient_search_text=[\"Space Exploration Technologies\"], award_type_codes=[A,B,C,D], time_period; Tx detail: /api/v2/awards/{id}/","None","Free","Daily","T2 Daily","JSON","Primary","Includes modifications (obligation changes)"),
("SpaceX contracts","Contract award records (replaces FPDS, retired Feb 2026)","SAM.gov Contract Awards API","REST","https://api.sam.gov/contract-awards/v1/search?api_key={KEY}&awardeeLegalBusinessName=SPACE EXPLORATION TECHNOLOGIES CORP","contractingDepartmentCode=8000 (NASA), 9700 (DoD); lastModifiedDate range","API key (SAM.gov / Login.gov)","Free; 10 req/day without role, 1,000 with role","Daily","T2 Daily","JSON","Primary","Get a system account for 1,000+/day"),
("SpaceX contracts","Upcoming solicitations SpaceX may win (lunar, NSSL, Golden Dome)","SAM.gov Contract Opportunities API","REST","https://api.sam.gov/opportunities/v2/search?api_key={KEY}&postedFrom={MM/dd/yyyy}&postedTo={MM/dd/yyyy}&organizationName=NATIONAL AERONAUTICS AND SPACE ADMINISTRATION","Filter NAICS 336414, 336415, 481212, 517410","API key","Free","Daily","T2 Daily","JSON","Secondary","Pipeline view, not wins"),
("SpaceX contracts","Daily DoD contract announcements ≥$7.5M (Space Force, NRO launches)","Department of Defense – Contracts","Web / RSS","https://www.defense.gov/News/Contracts/","Published ~5pm ET weekdays; parse for 'Space Exploration Technologies'","None","Free","Daily","T2 Daily","HTML/RSS","Primary",""),
("SpaceX contracts","NSSL launch assignments & Space Force awards","Space Systems Command","Web","https://www.ssc.spaceforce.mil/News","","None","Free","Daily","T4 Event","HTML","Primary",""),
("SpaceX contracts","NASA contract awards (Artemis HLS, Crew/Cargo, launch services)","NASA News Releases","RSS","https://www.nasa.gov/news-release/feed/","Filter titles for 'contract', 'SpaceX', 'award'","None","Free","Hourly","T4 Event","RSS XML","Primary",""),
("SpaceX contracts","Bid protests of NASA/DoD awards (risk to wins)","GAO Bid Protest Decisions","Web","https://www.gao.gov/legal/bid-protests/search","Search 'Space Exploration Technologies' or 'SpaceX'","None","Free","Weekly","T4 Event","HTML","Optional",""),
("SpaceX customers","Starlink licensing, spectrum, direct-to-cell filings","FCC ECFS API + ICFS","REST","https://publicapi.fcc.gov/ecfs/filings?api_key={KEY}&q=SpaceX&sort=date_disseminated,DESC","Satellite applications: https://licensing.fcc.gov/myibfs/ (ICFS); api.data.gov key","API key (api.data.gov)","Free","Daily","T4 Event","JSON","Primary","Spectrum approvals drive Starlink capacity"),
("SpaceX customers","Backlog, Starlink subscribers, revenue by segment","SEC EDGAR – SPCX 10-Q/10-K/8-K","REST","https://data.sec.gov/submissions/CIK{SPCX CIK}.json","XBRL: /api/xbrl/companyfacts/CIK{SPCX CIK}.json; resolve CIK via company_tickers.json","None (User-Agent)","Free","Quarterly + 15-min 8-K watch","T3 Scheduled","JSON","Primary","Remaining performance obligations = order backlog"),
("SpaceX customers","Commercial customer announcements (airlines, telcos, governments)","Starlink updates","Web","https://www.starlink.com/updates","Also Starlink business/aviation press pages","None","Free","Daily","T4 Event","HTML","Secondary",""),
# ---------- SpaceX news ----------
("SpaceX news","Space-industry articles & reports linked to launches","Spaceflight News API (SNAPI) v4","REST","https://api.spaceflightnewsapi.net/v4/articles/?search=SpaceX&ordering=-published_at","Also /v4/reports/, /v4/blogs/; filter launch=<LL2 launch id>","None","Free","15 min","T1 Real-time","JSON","Primary","Same org as Launch Library 2, IDs link across"),
("SpaceX news","SPCX ticker news + sentiment","Finnhub / Alpha Vantage / Massive","REST","https://finnhub.io/api/v1/company-news?symbol=SPCX&from={d}&to={d}&token={KEY}","AV: function=NEWS_SENTIMENT&tickers=SPCX","API key","Free tiers","15 min","T1 Real-time","JSON","Primary","Reuse existing news agents with SPCX ticker"),
("SpaceX news","Headline RSS (SpaceX, Starship, Starlink, NASA contract)","Google News RSS","RSS","https://news.google.com/rss/search?q=SpaceX+OR+Starlink+OR+Starship&hl=en-US&gl=US&ceid=US:en","Robotaxi feed: q=Tesla+Robotaxi+OR+Cybercab","None","Free","15 min","T1 Real-time","RSS XML","Backup",""),
("Calendars","NYSE trading sessions, holidays & 1pm early closes","exchange_calendars (Python library, calendar XNYS)","Library (offline)","https://pypi.org/project/exchange-calendars/","xc.get_calendar('XNYS').is_session(date); session_close(date) for early closes","None","Free (open source)","","","Python","Primary","No API calls; upgrade the package yearly so new-year holidays are included"),
("Calendars","US federal, China (incl. make-up days) & Netherlands public holidays","holidays (Python library)","Library (offline)","https://pypi.org/project/holidays/","holidays.US(), holidays.financial_holidays('NYSE'), holidays.CN(language='en_US'), holidays.NL()","None","Free (open source)","","","Python","Primary","China dates for next year only appear once the State Council publishes them"),
("Calendars","Market calendar check incl. early closes (confirms library)","Alpaca Calendar API","REST","https://paper-api.alpaca.markets/v2/calendar?start={YYYY-MM-01}&end={YYYY-MM-last}","Returns each trading date with open/close times","API key + secret (free account)","Free","","","JSON","Secondary","One call a month; alert if it disagrees with exchange_calendars"),
("Calendars","Official NYSE holiday & early-close list","NYSE","Web","https://www.nyse.com/markets/hours-calendars","Published ~3 years ahead","None","Free","","","HTML","Secondary","Annual check against the library"),
("Calendars","US bond-market (SIFMA) closures & early closes","SIFMA holiday recommendations","Web","https://www.sifma.org/resources/general/holiday-schedule/","Governs Treasury yield & NY Fed rate publication days","None","Free","","","HTML","Secondary","Bond market closes on Columbus & Veterans Day even though stocks trade"),
("Calendars","US federal holidays (agency publishing days)","US Office of Personnel Management (OPM)","Web","https://www.opm.gov/policy-data-oversight/pay-leave/federal-holidays/","","None","Free","","","HTML","Backup","Annual check"),
("Calendars","China public holidays & weekend make-up workdays","State Council General Office (gov.cn)","Web","https://www.gov.cn/zhengce/","Annual holiday notice (usually Nov–Dec for the next year)","None","Free","","","HTML (Chinese)","Secondary","Used to skip MOFCOM/MIIT sources on China holidays"),
("Calendars","Public holidays by country (backup)","Nager.Date API","REST","https://date.nager.at/api/v3/PublicHolidays/{year}/{US|CN|NL}","","None","Free","","","JSON","Backup","Only if the Python library is unavailable"),
("Interest rates","Market-implied fed funds rate probabilities (replaces CME FedWatch)","Atlanta Fed Market Probability Tracker","Web + data download","https://www.atlantafed.org/cenfis/market-probability-tracker","Historical data download link on the page; RSS: https://www.atlantafed.org/RSS/MarketProbabilityTracker","None","Free","","","XLSX/CSV","Primary","Derived from CME SOFR options; updated each business day with prior-day data. Quarterly horizons, not meeting-by-meeting"),
("Interest rates / Inflation","Meeting-level Fed decision odds and CPI outcome odds","Kalshi public market data API","REST","https://api.elections.kalshi.com/trade-api/v2/markets?series_ticker={SERIES}&status=open","Find series via /trade-api/v2/series (e.g., Fed decision and CPI series); market data needs no login","None for market data","Free (~20 req/s)","","","JSON","Secondary","Prediction-market prices; thin markets can be noisy. Read-only use, no account needed"),
("Inflation","CPI, core CPI, PCE & core PCE nowcasts (free stand-in for consensus forecasts)","Cleveland Fed Inflation Nowcasting","Web","https://www.clevelandfed.org/indicators-and-data/inflation-nowcasting","Month-over-month and year-over-year nowcasts; updated each business day ~10:00 ET","None","Free","","","HTML/JSON (page data)","Primary","CPI surprise = actual (BLS) − Cleveland Fed nowcast on the day before release"),
("Stock price","Daily index closes (fallback for ETF proxies): S&P 500, Dow, Nasdaq-100","FRED","REST","https://api.stlouisfed.org/fred/series/observations?series_id=SP500&api_key={KEY}&file_type=json","series_id = SP500, DJIA, NASDAQ100 (Russell 2000 and sector indexes not on FRED – use DS-01/DS-05)","API key (free)","Free","","","JSON","Backup","Index levels, not ETF prices; one-day lag. Used only if Alpaca and Massive both fail"),
("Short interest","Short interest: shares short, days to cover, % change; % of float derived","FINRA Query API – consolidated short interest","REST (POST)","https://api.finra.org/data/group/otcMarket/name/consolidatedShortInterest","Body: compareFilters on symbolCode IN (TSLA, SPCX, …) and settlementDate; fields e.g. currentShortPositionQuantity, daysToCoverQuantity (confirm names in the FINRA API Console)","Free FINRA API account (client credentials); confirm dataset entitlement","Free (non-commercial)","","","JSON/CSV","Primary","Covers exchange-listed and OTC stocks. Twice monthly on FINRA publication dates (see Release Calendar tab). % of float = short shares ÷ float (shares outstanding from SEC dei:EntityCommonStockSharesOutstanding as proxy)"),
("Options sentiment","Put/call ratio (volume & open interest) and IV30 for TSLA, SPCX","Alpaca options market data – indicative feed","REST","https://data.alpaca.markets/v1beta1/options/snapshots/{UNDERLYING}?feed=indicative","One chain snapshot per underlying after the close; IV30 = interpolate implied vol of contracts ~30 days to expiry; P/C = put ÷ call daily volume","API key + secret (free account)","Free Basic plan: indicative feed (15-min delayed) – CONFIRM free tier includes chain snapshots with IV/greeks","","","JSON","Secondary","Confirm free tier includes it before building; if greeks/IV are missing, compute IV from option mid prices (Black-Scholes) or drop IV30"),
]

# ---------------- Batch schedule (all times ET; nothing runs more often than hourly) ----------------
JOBS = [
# id, cadence_order, cadence, name, run time ET, run time PT, days (calendar rule), runs/month, writes to, holiday calendar
("H1",1,"Hourly","Market prices","Hourly at :05, 10:05–16:05 (early-close days: 10:05–13:05)","Hourly at :05, 07:05–13:05 (early-close days: 07:05–10:05)","NYSE trading days only",1751/12,"prices_hourly (1-hour bars; last run = daily close)","US-MKT"),
("H2",1,"Hourly","News & sentiment","Hourly at :15, 24 runs","Hourly at :15, 24 runs","NYSE trading days only; first run after a weekend/holiday pulls everything since the last run",24*251/12,"news_articles (dedupe on URL hash)","US-MKT"),
("H3",1,"Hourly","Regulatory & company feeds","Hourly at :25, 06:25–22:25","Hourly at :25, 03:25–19:25","US federal business days; catch-up on first run after a gap",17*250/12,"events (event_ts, type, source_url) + filings","US-FED"),
("D1",2,"Daily","Morning macro & calendars","07:00","04:00","US federal business days",250/12,"macro_daily, release_calendar","US-FED"),
("D2",2,"Daily","Regulatory & operations sweep","07:30","04:30","US federal business days; China/NL sources skipped on their own holidays",250/12,"events, robotaxi_fleet, launches","US-FED"),
("D3",2,"Daily","Overnight rates","09:15","06:15","US federal business days",250/12,"macro_daily","US-FED"),
("D4",2,"Daily","Market close","16:45","13:45","US bond-market (SIFMA) business days",249/12,"macro_daily (yield curve, FedWatch odds)","US-BOND"),
("D5",2,"Daily","Government contracts","17:45","14:45","US federal business days",250/12,"contracts (award_id, agency, obligated $)","US-FED"),
("W1",3,"Weekly","Weekly sweep","Mon 08:00","Mon 05:00","Monday; if Monday is a US federal holiday, runs the next business day",4.33,"events, robotaxi_fleet, risk_indices","US-FED"),
("M1",4,"Monthly / on release","Release-triggered","At release time (see Run Time); dates come from D1 calendars","3 hours earlier than ET","Release days only (agencies don't release on holidays)",1,"releases (actual, consensus, surprise, release_ts)","Release calendar"),
("C1",4,"Monthly / on release","Calendar refresh","1st US federal business day of month, 06:00","03:00","1st US federal business day of month",1,"calendar_days (date, calendar, is_open, close_time)","US-FED"),
("Q1",5,"Quarterly","Earnings & quarterly reports","Earnings day 16:35 + next day 08:00","13:35 + next day 05:00","Earnings / quarter-end (NYSE trading days)",0.33,"fundamentals_quarterly","US-MKT"),
("A1",6,"Annual","Annual reports & holiday lists","On publication (early in the year); holiday lists each December","—","Once a year",1/12,"robotaxi_fleet (annual), calendar_days","—"),
("O1",7,"One-time","Historical backfill","At setup","—","Once",0,"history tables","—"),
("F1",8,"Failover only","Backup sources","Only when the primary source fails","—","On failure (same calendar as primary)",0,"same table as primary","Same as primary"),
("X0",9,"Not used / merged","Not collected (paid) or merged into another call","See Schedule Note","—","—",0,"—","—"),
]
CAL_NAMES={"US-MKT":"US-MKT (NYSE trading days)","US-FED":"US-FED (US federal business days)","US-BOND":"US-BOND (SIFMA bond-market days)","CN":"CN (China State Council holidays)","NL":"NL (Netherlands public holidays)"}
SRC_CAL={92:"Release calendar",93:"US-MKT",91:"US-FED",88:"US-FED",89:"US-MKT",90:"US-FED",33:"CN",59:"CN",58:"NL",5:"US-MKT",13:"US-MKT",2:"US-MKT",3:"US-MKT",4:"US-MKT",42:"US-MKT",43:"US-MKT",23:"US-FED"}
J={j[0]:j for j in JOBS}
# per-row schedule: id -> (job, run time ET (blank = job default), calls per run, schedule note)
S={
1:("D4","",9,"Free tier: daily close per ticker (TSLA, SPCX + 7 ETFs), throttled to 5/min; failover for hourly bars"),
2:("H1","",1,"Primary: one multi-symbol call returns hourly bars for TSLA, SPCX and the 7 ETF proxies (union of all users' tickers)"),3:("F1","",2,""),4:("F1","",2,""),
5:("O1","At setup + when a user adds a ticker",9,"5-year daily backfill: one call per ticker (9 at setup); no recurring calls"),
6:("H3","",1,"One EDGAR submissions call for SPCX catches new 8-K, Form 4, 10-Q"),
7:("O1","",0,"Pre-IPO history only"),
8:("H3","",1,"One call returns all upcoming SpaceX launches"),
9:("H3","",1,""),
10:("H3","",1,"One EDGAR submissions call for TSLA"),
11:("Q1","Day after Tesla earnings, 08:00",1,""),
12:("Q1","Deliveries: ~2nd day after quarter end, 09:05; earnings: 16:35",1,"2 events per quarter"),
13:("D1","",2,"TSLA + SPCX"),
14:("D4","",1,"Whole yield curve in one call (published ~15:30 ET)"),
15:("D1","",6,"DGS2, DGS10, DGS30, T10Y2Y, DFII10, T10YIE"),
16:("D1","",3,"DFEDTARU, DFEDTARL, EFFR"),
17:("D3","",2,"EFFR + SOFR (published ~09:00 ET)"),
18:("H3","",1,"Plus one extra run at 14:05 ET on FOMC decision days"),
19:("M1","1st business day of month, 08:00",1,"Calendar + SEP check"),
20:("X0","",0,"Paid API not used – replaced by DS-88 (Atlanta Fed) and DS-89 (Kalshi)"),
21:("M1","08:35 on BLS CPI release day",1,"One POST returns all CPI series"),
22:("M1","1st business day of month, 08:00",1,"Refresh CPI release dates"),
23:("F1","",4,"Backup for BLS/BEA"),
24:("M1","08:35 on BEA PCE release day",1,""),
25:("X0","",0,"Paid calendar not used – dates from DS-22/DS-26, expectations from DS-90 (and DS-89)"),
26:("D1","",1,"Source for release-day triggers (CPI, PCE) – replaces paid calendar"),
27:("H3","",1,"One query combines tariff terms + USTR/ITA/BIS/CBP agencies; also covers DS-49"),
28:("H3","",1,""),29:("H3","",1,""),
30:("W1","",1,""),
31:("D2","",1,""),32:("D2","",1,""),33:("D2","",1,""),
34:("M1","08:35 on Census trade release day",2,"Imports + exports"),
35:("H2","",1,"One combined query (tariff OR sanctions OR Taiwan OR ...)"),
36:("W1","",1,""),37:("D2","",1,""),38:("W1","",1,""),
39:("D1","",1,""),
40:("H2","Hourly at :15, 06:15–21:15 only (16 runs)",1,"One ticker per run from a fixed rotation over the union of users' tickers; hard cap 16/day (free limit 25)"),
41:("H2","",2,"TSLA + SPCX; also covers DS-78"),
42:("F1","",1,""),43:("X0","",0,"Not used – free tier is 24h delayed and not licensed for production; news covered by DS-41, DS-44"),
44:("H2","",3,"3 queries: Tesla, SpaceX/Starlink/Starship, Robotaxi/Cybercab; also covers DS-79"),
45:("D1","",3,"VIXCLS, DTWEXBGS, DCOILWTICO"),
46:("M1","1st Monday of month, 08:00",2,"ADS + ADAS CSVs"),
47:("D2","",1,""),48:("D2","",3,"Model Y, Model 3, Cybercab"),
49:("X0","",0,"Covered by DS-27 Federal Register query"),
50:("W1","",1,""),51:("W1","",1,""),
52:("A1","",1,""),
53:("Q1","When CPUC posts quarterly AV data",1,""),
54:("W1","",1,""),55:("W1","",1,""),56:("W1","",1,""),57:("W1","",1,""),58:("W1","",1,""),59:("W1","",1,""),
60:("D2","",1,""),61:("D2","",1,""),62:("W1","",1,""),63:("W1","",1,""),
64:("D2","",1,""),65:("D2","",1,"CelesTrak asks for ≤1 download per 2 hours; daily is well within"),
66:("W1","",1,""),
67:("D5","",1,""),68:("D5","",1,"Fits 10/day limit of a basic SAM.gov key"),69:("D5","",1,""),
70:("D5","",1,"Announcements post ~17:00 ET"),
71:("D2","",1,""),72:("H3","",1,""),73:("W1","",1,""),
74:("D2","",1,""),
75:("Q1","Day after SPCX earnings, 08:00",1,"XBRL backlog/subscribers; 8-K watch covered by DS-06"),
76:("D2","",1,""),
77:("H2","",1,""),
78:("X0","",0,"SPCX added to DS-40/DS-41 calls"),
79:("X0","",0,"Covered by DS-44 Google News RSS queries"),
80:("C1","",0,"Offline library – no API calls"),81:("C1","",0,"Offline library – no API calls"),
82:("C1","",1,"One call per month"),83:("A1","",1,"December check of next year's list"),84:("A1","",1,"December check"),
85:("A1","",1,"December check"),86:("A1","",1,"Check when the notice is published (usually Nov–Dec)"),87:("F1","",3,"US, CN, NL"),
91:("F1","",3,"SP500, DJIA, NASDAQ100"),92:("M1","On FINRA publication date (twice monthly), 08:00",1,"One call per publication returns all tracked tickers"),93:("D4","",2,"One chain snapshot per underlying (TSLA, SPCX)"),
88:("D1","",1,"Prior-day data available by morning"),89:("D4","",2,"Fed decision series + CPI series"),90:("D4","",1,"Captures the latest nowcast (updated ~10:00 ET)"),
}
assert len(S)==len(rows)
RUNS_OVERRIDE={40:16*251/12,12:0.67,92:2}
# ---------------- Cost & priority ----------------
# id -> (priority, current $/mo, paid plan, paid $/mo, likelihood of needing paid plan, trigger)
PRI={92:"P2",93:"P2",91:"P3",2:"P1",26:"P1",88:"P2",89:"P2",90:"P1",80:"P1",81:"P1",82:"P2",83:"P2",84:"P2",86:"P2",1:"P3",6:"P1",10:"P1",11:"P1",12:"P1",14:"P1",15:"P1",16:"P1",18:"P1",19:"P1",21:"P1",22:"P1",25:"P1",27:"P1",28:"P1",29:"P1",
 41:"P1",46:"P1",47:"P1",50:"P1",54:"P1",55:"P1",60:"P1",64:"P1",67:"P1",70:"P1",72:"P1",75:"P1",
 8:"P2",13:"P2",17:"P2",20:"P2",24:"P2",26:"P2",30:"P2",31:"P2",32:"P2",35:"P2",37:"P2",38:"P2",40:"P2",44:"P2",48:"P2",51:"P2",53:"P2",
 56:"P2",58:"P2",59:"P2",65:"P2",68:"P2",71:"P2",74:"P2",76:"P2",77:"P2"}
FREE="Not used – free tier only ($0 data budget)"
IMPACT={  # free-tier impact written into the Notes column
 1:"FREE-TIER IMPACT: demoted to backup. Free Basic gives end-of-day + 15-min delayed data at 5 calls/min – enough for a daily-close check (9 calls/day incl. ETF proxies, throttled over ~2 min) and hourly failover, not primary intraday use. Fallback for index closes: FRED (DS-91).",
 2:"FREE-TIER IMPACT: now the primary price source; ETF proxies ride in the same call (still 1 call/run). Prices match the consolidated tape closely; volume is IEX-only (a small share of total volume), so volume charts show trend, not true totals. No real-time SIP feed.",
 3:"FREE-TIER IMPACT: shares the 25 calls/day free limit with DS-40 news; only used if Alpaca and Massive both fail.",
 4:"FREE-TIER IMPACT: none – free 60 calls/min covers failover quotes.",
 8:"FREE-TIER IMPACT: none – free 15 calls/hour; schedule uses 1/hour.",
 20:"FREE-TIER IMPACT: paid API dropped. Meeting-by-meeting FedWatch odds are no longer collected; replaced by Atlanta Fed quarterly-horizon probabilities (DS-88) and Kalshi meeting odds (DS-89).",
 21:"CPI surprise is now actual (this source) minus the Cleveland Fed nowcast (DS-90) instead of Wall Street consensus.",
 22:"FREE-TIER IMPACT: now a primary source of CPI release dates (replaces paid calendar DS-25).",
 25:"FREE-TIER IMPACT: paid calendar dropped. No consensus forecasts: release dates come from DS-22/DS-26 and expectations from the Cleveland Fed nowcast (DS-90), so 'surprise' is measured vs a model, not analysts.",
 26:"FREE-TIER IMPACT: promoted to primary for release dates (CPI, PCE, jobs). Gives dates only – no consensus values.",
 36:"FREE-TIER IMPACT: personal email gets the Open tier (aggregated data only, no event-level API). Conflict events come mainly from GDELT (DS-35).",
 40:"FREE-TIER IMPACT: capped at 16 calls/day (06:15–21:15 ET) to stay under the free 25/day. Collectors read the union of all users' tickers but sentiment rotates one ticker per call (fixed list or rotation), so each ticker refreshes every few hours, not hourly; no overnight updates.",
 43:"FREE-TIER IMPACT: not used. Free Developer tier is 24h delayed and not licensed for production; news comes from Finnhub (DS-41) and Google News RSS (DS-44).",
}
C={}   # every source runs on its free tier; no paid plans
DECLINED=[  # id, original priority, paid plan declined, price avoided $/mo, free replacement / how the free tier is used
 (25,"P1","FMP Premium / Finnhub premium economic calendar",59,"Release dates: BLS schedule (DS-22) + FRED releases (DS-26). Expectations: Cleveland Fed nowcast (DS-90) + Kalshi CPI odds (DS-89)"),
 (1,"P1","Massive Stocks Starter",29,"Alpaca free IEX hourly bars (DS-02) become primary; Massive free tier keeps the daily-close check"),
 (20,"P2","CME FedWatch API – end of day",25,"Atlanta Fed Market Probability Tracker (DS-88) + Kalshi Fed-decision odds (DS-89)"),
 (40,"P2","Alpha Vantage Premium",49.99,"Stay on free 25 calls/day: news polling capped at 16 calls/day"),
 (2,"P3","Alpaca Algo Trader Plus (SIP feed)",99,"Use free IEX feed; full-market volume not required for hourly trend"),
 (43,"P3","NewsAPI Business",449,"Dropped – Finnhub company news (DS-41) + Google News RSS (DS-44)"),
 (8,"P2","Launch Library 2 Patreon tier (est.)",5,"Free 15 calls/hour; we use 1/hour"),
 (36,"P3","ACLED Partner/Enterprise (unpriced)",0,"Open tier (aggregated data) + GDELT events (DS-35)"),
]

def et_to_pt(t):
    import re
    return re.sub(r'(\d{2}):(\d{2})',lambda m:f"{(int(m.group(1))-3)%24:02d}:{m.group(2)}",t)

recs=[]
for i,r in enumerate(rows,1):
    job,rt,cpr,note=S[i]; j=J[job]
    et=rt or j[4]; pt=et_to_pt(rt) if rt else j[5]
    runs=RUNS_OVERRIDE.get(i,j[7])
    lst=list(r); del lst[8:10]   # drop old Refresh Rate + Agent Tier
    if i in (20,25,43): lst[9]="Not used (paid)"
    if i==40: lst[7]="Free 25 calls/day (capped at 16/day by schedule)"
    if i in IMPACT: lst[10]=IMPACT[i]
    pr=PRI.get(i,"P3") if job!="X0" else "—"
    cur,plan,paid,lik,trig=C.get(i,(0,"Free – no paid plan needed" if i not in [d[0] for d in DECLINED] else FREE,0,0,next((d[4] for d in DECLINED if d[0]==i),"")))
    calc=SRC_CAL.get(i, j[9]); calname=CAL_NAMES.get(calc,calc)
    recs.append((j[1],et,i,[f"DS-{i:02d}",j[2],job,et,pt,cpr,runs],[pr,cur,plan,paid,lik,trig],lst+[note,calname]))
recs.sort(key=lambda x:(x[0],x[3][2],x[2]))

headers=["ID","Cadence","Batch Job","Run Time (ET)","Run Time (PT)","Calls / Run","Runs / Month","Calls / Month",
 "Priority","Current Cost ($/mo)","Paid Plan (if needed)","Paid Plan Cost ($/mo)","Likelihood of Paying","Likelihood Band","Expected Cost ($/mo)","Cost Trigger",
 "Category","Data Item","Source","Access Type","Endpoint / URL","Example Params / Notes on Call","Auth","Cost / Limits","Format","Role","Notes","Schedule Note","Holiday Calendar"]
wb=Workbook(); ws=wb.active; ws.title="Data Sources"
hfill=PatternFill("solid",start_color="1F3864"); thin=Side(style="thin",color="BFBFBF")
ws.append(headers)
for n_,(_,_,_,v,c,rest) in enumerate(recs,2):
    ws.append(v+[f"=F{n_}*G{n_}",c[0],c[1],c[2],c[3],c[4],
      f'=IF(L{n_}=0,"None",IF(M{n_}>0.6,"High",IF(M{n_}>0.2,"Medium","Low")))',f"=J{n_}+(L{n_}-J{n_})*M{n_}",c[5]]+rest)
widths=[8,13,8,22,22,8,8,9,8,10,30,10,10,10,10,40,18,30,26,13,55,45,18,22,10,9,36,40,30]
for c,w in enumerate(widths,1): ws.column_dimensions[get_column_letter(c)].width=w
cadfill={"Hourly":"FCE4D6","Daily":"E2EFDA","Weekly":"DDEBF7","Monthly / on release":"FFF2CC","Quarterly":"EDE7F6","Annual":"EDE7F6","One-time":"F2F2F2","Failover only":"F2F2F2","Not used / merged":"F2F2F2"}
for row in ws.iter_rows():
    for cell in row:
        cell.font=Font(name=F,size=9,bold=cell.row==1,color="FFFFFF" if cell.row==1 else "000000")
        cell.alignment=Alignment(wrap_text=True,vertical="top")
        cell.border=Border(top=thin,bottom=thin,left=thin,right=thin)
        if cell.row==1: cell.fill=hfill
for r in range(2,ws.max_row+1):
    ws.cell(r,2).fill=PatternFill("solid",start_color=cadfill[ws.cell(r,2).value])
    ws.cell(r,6).font=Font(name=F,size=9,color="0000FF"); ws.cell(r,7).font=Font(name=F,size=9,color="0000FF")
    ws.cell(r,7).number_format="0.##"; ws.cell(r,8).number_format="#,##0"
    if str(ws.cell(r,21).value).startswith("http"): ws.cell(r,21).font=Font(name=F,size=9,color="0563C1")
    ws.cell(r,9).fill=PatternFill("solid",start_color={"P1":"F4B183","P2":"FFE699","P3":"E2EFDA"}.get(ws.cell(r,9).value,"F2F2F2"))
    for c in (10,12): ws.cell(r,c).number_format='$#,##0.00;($#,##0.00);"-"'; ws.cell(r,c).font=Font(name=F,size=9,color="0000FF")
    ws.cell(r,13).number_format='0%;;"-"'; ws.cell(r,13).font=Font(name=F,size=9,color="0000FF")
    ws.cell(r,15).number_format='$#,##0.00;($#,##0.00);"-"'
    if ws.cell(r,12).value: 
        for c in (11,12,13): ws.cell(r,c).fill=PatternFill("solid",start_color="FFFF00")
last=ws.max_row
ws.freeze_panes="D2"
tab=Table(displayName="DataSources",ref=f"A1:{get_column_letter(len(headers))}{last}")
tab.tableStyleInfo=TableStyleInfo(name="TableStyleLight1",showRowStripes=False); ws.add_table(tab)

# ---------------- Batch Schedule sheet ----------------
b=wb.create_sheet("Batch Schedule",1)
b.append(["Job","Cadence","Job Name","Run Time (ET)","Run Time (PT)","Days","Source IDs","Runs / Month","Calls / Run (sum)","Calls / Month","Avg Calls / Day","Writes To","Holiday Calendar"])
for j in JOBS:
    ids=", ".join(v[3][0] for v in recs if v[3][2]==j[0])
    r_=b.max_row+1
    b.append([j[0],j[2],j[3],j[4],j[5],j[6],ids,j[7],
      f"=SUMIFS('Data Sources'!$F$2:$F${last},'Data Sources'!$C$2:$C${last},A{r_})",
      f"=SUMIFS('Data Sources'!$H$2:$H${last},'Data Sources'!$C$2:$C${last},A{r_})",
      f"=J{r_}/30",j[8],CAL_NAMES.get(j[9],j[9])])
tot=b.max_row+1
b.append(["TOTAL","","","","","","","","",f"=SUM(J2:J{tot-1})",f"=SUM(K2:K{tot-1})",""])
b.append([]); b.append(["Free-tier limit check (typical day, after batching)"])
lim_hdr=b.max_row+1
b.append(["Provider","Free limit","Our peak calls / day","Headroom","","","Source IDs"])
for p_,lim,ours,ids in [("Alpaca (free IEX + indicative options)","200 calls/min",9,"DS-02 (7), DS-93 (2)"),("Massive (free Basic)","5 calls/min",9,"DS-01 (throttled: max 5/min)"),("FINRA API (free)","Not published – 1 call per publication",1,"DS-92 (2/month)"),("Kalshi (public data)","~20 calls/sec",2,"DS-89"),("Alpha Vantage","25 calls/day",16,"DS-40"),("Finnhub (free)","60 calls/min",50,"DS-13, DS-41"),
 ("SAM.gov (basic key)","10 calls/day",2,"DS-68, DS-69"),("Launch Library 2","15 calls/hour",17,"DS-08 (1/hour)"),("FRED","120 calls/min",14,"DS-15, 16, 39, 45"),
 ("SEC EDGAR","10 calls/sec",34,"DS-06, DS-10"),("BLS (with key)","500 calls/day",1,"DS-21 (release days)")]:
    rr=b.max_row+1
    b.append([p_,lim,ours,("OK — 1 call/hour vs 15/hour" if "hour" in lim else "OK"),"","",ids])
for row in b.iter_rows():
    for cell in row:
        hdr=cell.row in (1,lim_hdr)
        cell.font=Font(name=F,size=9,bold=hdr or cell.row in (tot,lim_hdr-1),color="FFFFFF" if hdr else "000000")
        cell.alignment=Alignment(wrap_text=True,vertical="top")
        if hdr: cell.fill=hfill
        if cell.row<=tot or cell.row>=lim_hdr: cell.border=Border(top=thin,bottom=thin,left=thin,right=thin)
for r in range(2,tot):
    b.cell(r,2).fill=PatternFill("solid",start_color=cadfill[b.cell(r,2).value])
    for c in (8,): b.cell(r,c).number_format="0.##"; b.cell(r,c).font=Font(name=F,size=9,color="0000FF")
    for c in (9,10): b.cell(r,c).number_format="#,##0"
    b.cell(r,11).number_format="#,##0.0"
b.cell(tot,10).number_format="#,##0"; b.cell(tot,11).number_format="#,##0.0"
for c,w in enumerate([8,16,24,30,28,30,46,10,10,11,10,40,30],1): b.column_dimensions[get_column_letter(c)].width=w
b.freeze_panes="A2"
# Series IDs
s=wb.create_sheet("Series IDs")
sid=[("FRED","DGS2","2-Year Treasury yield","Daily","Bond yields"),
("FRED","DGS10","10-Year Treasury yield","Daily","Bond yields"),
("FRED","DGS30","30-Year Treasury yield","Daily","Bond yields"),
("FRED","T10Y2Y","10Y minus 2Y spread","Daily","Bond yields"),
("FRED","DFII10","10-Year TIPS (real) yield","Daily","Bond yields"),
("FRED","DFEDTARU","Fed funds target – upper","Daily","Interest rates"),
("FRED","DFEDTARL","Fed funds target – lower","Daily","Interest rates"),
("FRED","EFFR","Effective fed funds rate","Daily","Interest rates"),
("FRED","CPIAUCSL","CPI-U all items, SA","Monthly","Inflation"),
("FRED","CPILFESL","Core CPI (ex food & energy), SA","Monthly","Inflation"),
("FRED","PCEPI","PCE price index","Monthly","Inflation"),
("FRED","PCEPILFE","Core PCE price index","Monthly","Inflation"),
("FRED","T10YIE","10-Year breakeven inflation","Daily","Inflation"),
("FRED","USEPUINDXD","Economic Policy Uncertainty (daily)","Daily","Geopolitical"),
("FRED","VIXCLS","CBOE VIX","Daily","Market context"),
("FRED","DTWEXBGS","Broad US dollar index","Daily","Market context"),
("FRED","DCOILWTICO","WTI crude oil","Daily","Market context"),
("BLS","CUSR0000SA0","CPI-U all items, SA","Monthly","Inflation"),
("BLS","CUUR0000SA0","CPI-U all items, NSA (used for headline YoY)","Monthly","Inflation"),
("BLS","CUSR0000SA0L1E","Core CPI, SA","Monthly","Inflation"),
("BLS","CUSR0000SETA01","CPI – new vehicles, SA","Monthly","Inflation (auto-specific)"),
("SEC","0001318605","Tesla, Inc. CIK","—","Filings"),
("SEC","lookup via company_tickers.json","SpaceX (SPCX) CIK","—","Filings"),
("HTS","8703","Passenger motor vehicles","—","Tariffs"),
("HTS","8507.60","Lithium-ion batteries","—","Tariffs"),
("HTS","2805.30 / 2846","Rare-earth metals & compounds","—","Tariffs"),
("Ticker","TSLA","Tesla – Nasdaq","—","Stock price"),
("Ticker","SPCX","SpaceX – Nasdaq (listed 12 Jun 2026)","—","Stock price"),
("Ticker","SPY","S&P 500 ETF proxy","Hourly","Index proxy"),("Ticker","DIA","Dow Jones Industrial Average ETF proxy","Hourly","Index proxy"),
("Ticker","QQQ","Nasdaq-100 ETF proxy","Hourly","Index proxy"),("Ticker","IWM","Russell 2000 ETF proxy","Hourly","Index proxy"),
("Ticker","XLY","Consumer Discretionary sector ETF (Tesla's sector)","Hourly","Sector proxy"),("Ticker","ITA","Aerospace & Defense ETF (SpaceX peer group)","Hourly","Sector proxy"),
("Ticker","SMH","Semiconductor ETF (AI/chip exposure)","Hourly","Sector proxy"),
("FRED","SP500","S&P 500 index daily close (fallback)","Daily","Index proxy"),("FRED","DJIA","Dow Jones Industrial Average daily close (fallback)","Daily","Index proxy"),
("FRED","NASDAQ100","Nasdaq-100 index daily close (fallback)","Daily","Index proxy"),
("SEC XBRL","us-gaap:GrossProfit","Tesla gross profit (quarterly/annual)","Quarterly","Tesla fundamentals"),
("SEC XBRL","us-gaap:Revenues","Tesla total revenues; gross margin = GrossProfit ÷ Revenues","Quarterly","Tesla fundamentals"),
("SEC XBRL","dei:EntityCommonStockSharesOutstanding","Shares outstanding (float proxy for short interest % of float)","Quarterly","Short interest"),
("FINRA","otcMarket / consolidatedShortInterest","FINRA API group / dataset for short interest","Twice monthly","Short interest"),
("Alpaca","feed=indicative","Free options feed parameter for chain snapshots","Daily","Options sentiment"),
("NHTSA SGO","Reporting Entity = Tesla, Inc.","Filter for Tesla rows in ADS & ADAS crash CSVs","Monthly","Robotaxi"),
("NHTSA","AQ26002","Cybercab audit query (opened 3 Sep 2026; special order 10 Sep)","—","Robotaxi"),
("CPUC","TCP0046782-A","Tesla charter-party carrier permit (Bay Area)","—","Robotaxi"),
("Nevada NTA","AVNC Permit 002","Tesla AV network permit, ≤5,000 vehicles","—","Robotaxi"),
("USAspending","Space Exploration Technologies Corp","recipient_search_text value","—","SpaceX contracts"),
("SAM.gov","8000 / 9700","Contracting department codes: NASA / DoD","—","SpaceX contracts"),
("NAICS","336414","Guided missile & space vehicle manufacturing","—","SpaceX contracts"),
("NAICS","481212","Nonscheduled chartered freight air transport (launch services)","—","SpaceX contracts"),
("NAICS","517410","Satellite telecommunications (Starlink)","—","SpaceX contracts"),
("CelesTrak","GROUP=starlink","Starlink GP element set","Daily","SpaceX launches"),
("LL2","lsp__name=SpaceX","Launch Library 2 provider filter","Hourly","SpaceX launches")]
s.append(["Provider","Series / Code","Description","Frequency","Category"])
for r in sid: s.append(r)

for sh,w in [(s,[10,30,44,12,24])]:
    for c,x in enumerate(w,1): sh.column_dimensions[get_column_letter(c)].width=x
    for row in sh.iter_rows():
        for cell in row:
            cell.font=Font(name=F,size=9,bold=cell.row==1,color="FFFFFF" if cell.row==1 else "000000")
            cell.alignment=Alignment(wrap_text=True,vertical="top")
            cell.border=Border(top=thin,bottom=thin,left=thin,right=thin)
            if cell.row==1: cell.fill=hfill
    sh.freeze_panes="A2"


# ---------------- Cost Summary ----------------
cs=wb.create_sheet("Cost Summary",1)
D="'Data Sources'"
cs.append(["Monthly data cost by priority"])
cs.append(["Priority","Meaning","# Sources","Current ($/mo)","Worst Case – all paid plans ($/mo)","Expected ($/mo, probability-weighted)"])
for i_,(p_,m_) in enumerate([("P1","Must-have: dashboard is wrong or incomplete without it"),("P2","Should-have: improves signal; dashboard works without it"),("P3","Nice-to-have / backup / cross-check")],3):
    cs.append([p_,m_,f'=COUNTIF({D}!$I$2:$I${last},A{i_})',f'=SUMIFS({D}!$J$2:$J${last},{D}!$I$2:$I${last},A{i_})',
      f'=SUMIFS({D}!$L$2:$L${last},{D}!$I$2:$I${last},A{i_})',f'=SUMIFS({D}!$O$2:$O${last},{D}!$I$2:$I${last},A{i_})'])
cs.append(["TOTAL","","=SUM(C3:C5)","=SUM(D3:D5)","=SUM(E3:E5)","=SUM(F3:F5)"])
cs.append([])
cs.append(["Paid upgrades declined – free replacements in use"])
hdr2=cs.max_row+1
cs.append(["ID","Source","Priority","Paid Plan Declined","Price Avoided ($/mo)","","","Free Replacement / How the Free Tier Is Used"])
srcname={v[0]:rest[2] for (_,_,_,v,c,rest) in recs}
for i_,pr_,plan_,price_,repl_ in DECLINED:
    cs.append([f"DS-{i_:02d}",srcname[f"DS-{i_:02d}"],pr_,plan_,price_,"","",repl_])
endp=cs.max_row
cs.append(["TOTAL AVOIDED","","","",f"=SUM(E{hdr2+1}:E{endp})","","",""])
cs.append([])
for line in ["Policy (2026-10-03): $0 data budget – every source runs on its free tier. Paid Plan Cost and Likelihood of Paying are 0 on every row; to reconsider an upgrade, enter its price and likelihood on Data Sources and these totals recalculate.",
 "Trade-offs accepted: IEX-only volume (prices unaffected); quarterly-horizon rate odds from Atlanta Fed plus prediction-market odds instead of FedWatch meeting odds; Cleveland Fed nowcast instead of Wall Street consensus for CPI surprise; news capped at free limits.",
 "Prices checked 2026-10-03 from provider pricing pages / published summaries. FMP price is billed annually; Launch Library tier price is an estimate; ACLED commercial pricing is unpublished (excluded).",
 "Not included: hosting, database and scheduler costs for running the agents."]:
    cs.append([line])
for row in cs.iter_rows():
    for cell in row:
        hdr=cell.row in (2,hdr2)
        cell.font=Font(name=F,size=9,bold=hdr or cell.row in (1,6,hdr2-1,endp+1),color="FFFFFF" if hdr else "000000")
        cell.alignment=Alignment(wrap_text=cell.row<endp+2,vertical="top")
        if hdr: cell.fill=hfill
        if 2<=cell.row<=6 or hdr2<=cell.row<=endp+1: cell.border=Border(top=thin,bottom=thin,left=thin,right=thin)
for r in list(range(3,7))+list(range(hdr2+1,endp+2)):
    for c in (4,5,6,7): 
        if cs.cell(r,c).value is not None: cs.cell(r,c).number_format='$#,##0.00;($#,##0.00);"-"'
for r in range(hdr2+1,endp+1):
    cs.cell(r,5).number_format='$#,##0.00'
    cs.cell(r,3).fill=PatternFill("solid",start_color={"P1":"F4B183","P2":"FFE699","P3":"E2EFDA"}[cs.cell(r,3).value])
for c,w in enumerate([8,32,9,40,14,12,14,70],1): cs.column_dimensions[get_column_letter(c)].width=w
cs.column_dimensions["B"].width=40

# ---------------- Holiday Calendar sheet ----------------
import holidays as _hol, exchange_calendars as _xc, pandas as _pd, datetime as _dt, re as _re
_start,_end=_dt.date(2026,10,3),_dt.date(2027,12,31)
_xnys=_xc.get_calendar("XNYS",start="2025-01-01",end="2027-12-31")
_sess=set(d.date() for d in _xnys.sessions_in_range(str(_start),str(_end)))
_early={d.date():_xnys.session_close(d).tz_convert("America/New_York").strftime("%H:%M") for d in _xnys.sessions_in_range(str(_start),str(_end)) if _xnys.session_close(d).tz_convert("America/New_York").hour<16}
_us=_hol.US(years=[2026,2027]); _nyse=_hol.financial_holidays("NYSE",years=[2026,2027])
_cn=_hol.CN(years=[2026,2027],language="en_US"); _nl=_hol.NL(years=[2026,2027],language="en_US")
FED_JOBS="H3, D1, D2, D3, D5 skip (W1 moves to next business day)"
ev=[]
d=_start
while d<=_end:
    wd=d.weekday()<5
    if wd and d not in _sess:
        ev.append((d,"US-MKT","NYSE closed – "+_nyse.get(d,"market holiday"),"H1, H2, Q1 skip","All US-MKT sources"))
    if d in _early:
        ev.append((d,"US-MKT",f"NYSE early close ({_early[d]} ET)","H1 runs 10:05–13:05 ET only (last run = close)","DS-01 and backups"))
    if wd and d in _us:
        name=_us.get(d)
        mon=" – W1 moves to Tue" if d.weekday()==0 else ""
        ev.append((d,"US-FED","US federal holiday – "+name,FED_JOBS.replace(" (W1 moves to next business day)","")+mon,"All US-FED sources"))
        ev.append((d,"US-BOND","Bond market closed (SIFMA) – "+name,"D4 skips","DS-14, DS-20"))
    if d.year==2027 and d.month==3 and d.day==26:
        ev.append((d,"US-BOND","Good Friday – bond market normally closed (confirm SIFMA notice)","D4 skips","DS-14, DS-20"))
    if wd and d in _cn:
        prov=" (provisional until State Council notice)" if d.year==2027 else ""
        ev.append((d,"CN","China holiday – "+_cn.get(d)+prov,"D2 skips China sources","DS-33, DS-59"))
    if wd and d in _nl:
        ev.append((d,"NL","Netherlands holiday – "+_nl.get(d),"W1 skips RDW if Monday (picked up next week)" if d.weekday()==0 else "No job affected (RDW is weekly, Monday)","DS-58"))
    d+=_dt.timedelta(days=1)
# China make-up Saturdays/Sundays (official workdays) from 'substituted from' notes
for dd,name in _cn.items():
    for m in _re.findall(r"substituted from (\d\d)/(\d\d)/(\d{4})",name):
        md=_dt.date(int(m[2]),int(m[0]),int(m[1]))
        if _start<=md<=_end: ev.append((md,"CN","China make-up workday (weekend working day)","No US job runs on weekends; China sources picked up next US business day","DS-33, DS-59"))
ev=sorted(set(ev))
hc=wb.create_sheet("Holiday Calendar",3)
hc.append(["Date","Day","Calendar","Holiday / Event","Jobs Affected","Sources Affected"])
CALFILL={"US-MKT":"FCE4D6","US-FED":"DDEBF7","US-BOND":"E2EFDA","CN":"FFF2CC","NL":"EDE7F6"}
for e in ev:
    hc.append([e[0],e[0].strftime("%a"),e[1],e[2],e[3],e[4]])
hlast=hc.max_row
hc.append([])
for line in ["Rule: no job runs on Saturday or Sunday. Each job also skips the holidays of the calendar named on its row in Data Sources / Batch Schedule.",
 "Generated 2026-10-03 from exchange_calendars (NYSE) and the holidays library (US federal, China, Netherlands). The agents use the same libraries at run time (DS-80, DS-81), so this list is for reference.",
 "US-FED holidays that fall on a weekday but not NYSE holidays (Columbus Day, Veterans Day): stock jobs run, federal and bond jobs skip.",
 "China 2027 dates are the library's projection; replace with the State Council notice when published (usually Nov–Dec 2026).",
 "Weekend-only holidays are not listed because nothing runs on weekends anyway."]:
    hc.append([line])
for row in hc.iter_rows():
    for cell in row:
        cell.font=Font(name=F,size=9,bold=cell.row==1,color="FFFFFF" if cell.row==1 else "000000")
        cell.alignment=Alignment(wrap_text=cell.row<=hlast,vertical="top")
        if cell.row==1: cell.fill=hfill
        if cell.row<=hlast: cell.border=Border(top=thin,bottom=thin,left=thin,right=thin)
for r in range(2,hlast+1):
    hc.cell(r,1).number_format="yyyy-mm-dd"
    hc.cell(r,3).fill=PatternFill("solid",start_color=CALFILL[hc.cell(r,3).value])
for c,w in enumerate([11,6,10,52,52,22],1): hc.column_dimensions[get_column_letter(c)].width=w
hc.freeze_panes="A2"

# ---------------- Release Calendar sheet ----------------
rc=wb.create_sheet("Release Calendar",4)
rc.append(["Release","Reference / Settlement Date","Due Date","Publication Date (collect)","Job","Source"])
for st,du,pu in [("2026-09-30","2026-10-02","2026-10-09"),("2026-10-15","2026-10-19","2026-10-26"),("2026-10-30","2026-11-03","2026-11-10"),("2026-11-13","2026-11-17","2026-11-24"),("2026-11-30","2026-12-02","2026-12-09"),("2026-12-15","2026-12-17","2026-12-24"),("2026-12-31","2027-01-05","2027-01-12")]:
    rc.append(["FINRA short interest",_dt.date.fromisoformat(st),_dt.date.fromisoformat(du),_dt.date.fromisoformat(pu),"M1 (08:00 ET next business day if after hours)","DS-92"])
rc.append(["FINRA short interest – rest of 2027","Pending","Pending","Mid-month and month-end settlements; FINRA publishes the next year's schedule late in the year – add when published","M1","DS-92"])
rc.append(["CPI, PCE, trade, FOMC","—","—","Pulled automatically each morning by D1 from BLS schedule (DS-22) and FRED releases (DS-26)","M1","DS-21, DS-24, DS-34"])
rc.append(["Tesla deliveries","Quarter end","—","~2nd day after quarter end (UI plots on this release date)","Q1","DS-12"])
rl=rc.max_row
for row in rc.iter_rows():
    for cell in row:
        cell.font=Font(name=F,size=9,bold=cell.row==1,color="FFFFFF" if cell.row==1 else "000000")
        cell.alignment=Alignment(wrap_text=True,vertical="top")
        cell.border=Border(top=thin,bottom=thin,left=thin,right=thin)
        if cell.row==1: cell.fill=hfill
for r in range(2,rl+1):
    for c in (2,3,4):
        if isinstance(rc.cell(r,c).value,_dt.date): rc.cell(r,c).number_format="yyyy-mm-dd"
for c,w in enumerate([26,22,14,48,34,18],1): rc.column_dimensions[get_column_letter(c)].width=w
rc.freeze_panes="A2"

n=wb.create_sheet("Notes")
for line in ["Investor Dashboard – Data Source Catalog","Compiled 2026-10-03. Endpoints verified against public docs as of this date; free-tier limits change often — confirm before production use.",
"Collection rule: nothing is polled more often than once an hour. Sources are sorted by cadence (Hourly → Daily → Weekly → Monthly/on release → Quarterly → Annual → One-time → Failover → Not used / merged), then by batch job and ID.",
"Batching: each Batch Job runs all of its sources in one scheduled pass, so the scheduler wakes up 14 times a day rather than once per source. See the Batch Schedule tab for job times and call totals (formulas).",
"Call-reduction rules applied: (1) multi-ticker / multi-term queries combined into one call where the API allows; (2) backups are polled only when the primary fails (Failover only = 0 calls normally); (3) duplicate sources merged (DS-49, DS-78, DS-79); (4) release data (CPI, PCE, trade) fetched once at release time, using dates pulled by job D1.",
"Blue numbers (Calls / Run, Runs / Month) are editable assumptions; Calls / Month and the Batch Schedule totals recalculate. Runs / Month uses 251 NYSE trading days, 250 US federal business days and 249 bond-market days per year (2026 and 2027), divided by 12. Avg Calls / Day divides by 30 calendar days.",
"No collection on weekends or holidays: every job follows the Holiday Calendar named on its row (see the Holiday Calendar tab). Regional sources (China MOFCOM/MIIT, Netherlands RDW) are also skipped on that country's holidays. Weekly jobs move to the next business day; feeds catch up on the first run after a gap, so nothing is lost.",
"Times are US Eastern (source release times are published in ET); PT column = ET − 3 hours.",
"{KEY} = your API key; {d} = date YYYY-MM-DD; {TICKER} = any tracked ticker.",
"Ticker scope: collectors read the UNION of all users' tickers (default TSLA, SPCX + ETF proxies SPY, DIA, QQQ, IWM, XLY, ITA, SMH). Alpaca and Massive scale by adding symbols to the same calls; Alpha Vantage sentiment stays capped at 16 calls/day using a fixed list or rotation, so more tickers = less frequent sentiment per ticker.",
"New ticker added by a user: run the DS-05 5-year daily backfill once for that ticker, then it joins the hourly (DS-02) and daily-close (DS-01) calls.",
"SEC EDGAR requires a descriptive User-Agent header (name + email) and ≤10 requests/second.",
"Role: Primary = authoritative/first choice; Backup = use if primary fails or for cross-check; Fallback = unofficial; Optional = beyond original scope but useful.",
"Robotaxi caveat: Tesla does not disclose fleet size; TxDMV AV registrations are the best official proxy. Unofficial trackers are flagged 'Secondary' — cross-check before acting.",
"$0 data budget: all sources use free tiers; paid upgrades declined are listed on the Cost Summary tab with their free replacements. Cost: see Cost Summary tab. Priority P1/P2/P3 and Likelihood of Paying are on each Data Sources row; expected monthly cost is probability-weighted."]:
    n.append([line])
n.column_dimensions["A"].width=140
for row in n.iter_rows():
    for c in row: c.font=Font(name=F,size=10,bold=c.row==1); c.alignment=Alignment(wrap_text=True)
wb.save("investor_dashboard_data_sources.xlsx")
print(len(recs))
