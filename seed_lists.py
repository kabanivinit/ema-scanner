"""Built-in copy of the Nasdaq-100 members, used only if every online source fails (so the scanner never starts empty).
Copied from stockanalysis.com on 01 Nov 2026. Membership changes a few times a year; the live download replaces it."""

NASDAQ100_SEED = (
    "NVDA=NVIDIA|AAPL=Apple|MSFT=Microsoft|MU=Micron Technology|AMZN=Amazon.com|AMD=Advanced Micro Devices|"
    "TSLA=Tesla|META=Meta Platforms|GOOGL=Alphabet (A)|GOOG=Alphabet (C)|SPCX=Space Exploration Technologies|"
    "WMT=Walmart|AVGO=Broadcom|INTC=Intel|PLTR=Palantir Technologies|COST=Costco Wholesale|CSCO=Cisco Systems|"
    "PANW=Palo Alto Networks|AMAT=Applied Materials|LRCX=Lam Research|NFLX=Netflix|CRWD=CrowdStrike|"
    "TXN=Texas Instruments|AMGN=Amgen|LIN=Linde|SHOP=Shopify|MRVL=Marvell Technology|GILD=Gilead Sciences|"
    "KLAC=KLA|ADI=Analog Devices|PEP=PepsiCo|QCOM=Qualcomm|SNDK=Sandisk|ISRG=Intuitive Surgical|"
    "TMUS=T-Mobile US|ASML=ASML Holding|STX=Seagate Technology|VRTX=Vertex Pharmaceuticals|FTNT=Fortinet|"
    "WDC=Western Digital|ADP=Automatic Data Processing|BKNG=Booking Holdings|SBUX=Starbucks|ADBE=Adobe|"
    "MAR=Marriott International|CSX=CSX|INTU=Intuit|MELI=MercadoLibre|ARM=Arm Holdings|MNST=Monster Beverage|"
    "DDOG=Datadog|CEG=Constellation Energy|CTAS=Cintas|CDNS=Cadence Design Systems|SNPS=Synopsys|"
    "MDLZ=Mondelez International|REGN=Regeneron Pharmaceuticals|CMCSA=Comcast|MRNA=Moderna|APP=AppLovin|"
    "ROST=Ross Stores|DASH=DoorDash|ORLY=O'Reilly Automotive|ABNB=Airbnb|AEP=American Electric Power|"
    "HON=Honeywell International|LITE=Lumentum|FAST=Fastenal|NXPI=NXP Semiconductors|FANG=Diamondback Energy|"
    "PCAR=PACCAR|MPWR=Monolithic Power Systems|BKR=Baker Hughes|MSTR=Strategy|PDD=PDD Holdings|ADSK=Autodesk|"
    "HONA=Honeywell Aerospace|PYPL=PayPal|XEL=Xcel Energy|TER=Teradyne|CCEP=Coca-Cola Europacific Partners|"
    "KDP=Keurig Dr Pepper|TRI=Thomson Reuters|EXC=Exelon|ALAB=Astera Labs|IDXX=IDEXX Laboratories|"
    "ROP=Roper Technologies|PAYX=Paychex|WDAY=Workday|MCHP=Microchip Technology|TTWO=Take-Two Interactive|"
    "NBIS=Nebius Group|ODFL=Old Dominion Freight Line|FER=Ferrovial|AXON=Axon Enterprise|GEHC=GE HealthCare|"
    "RKLB=Rocket Lab|ALNY=Alnylam Pharmaceuticals|DXCM=DexCom|CPRT=Copart|CRWV=CoreWeave"
)


def nasdaq100_seed():
    import pandas as pd
    rows = [p.split("=", 1) for p in NASDAQ100_SEED.split("|")]
    return pd.DataFrame({"symbol": [r[0] for r in rows], "name": [r[1] for r in rows]})
