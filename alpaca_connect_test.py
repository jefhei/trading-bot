"""
Alpaca Connection Test
Tests basic API connectivity and account balance retrieval.
"""
import os
from dotenv import load_dotenv
import alpaca_trade_api as tradeapi

# Load environment variables from .env file
load_dotenv()

# Get credentials from environment
API_KEY = os.getenv("ALPACA_API_KEY")
SECRET_KEY = os.getenv("ALPACA_SECRET_KEY")
PAPER = os.getenv("ALPACA_PAPER", "true").lower() == "true"

if not API_KEY or not SECRET_KEY:
    raise ValueError(
        "Missing Alpaca credentials. Please set ALPACA_API_KEY and ALPACA_SECRET_KEY "
        "in your .env file. See .env.example for format."
    )

# Set base URL based on paper/live
BASE_URL = "https://paper-api.alpaca.markets" if PAPER else "https://api.alpaca.markets"

# Initialize API
api = tradeapi.REST(API_KEY, SECRET_KEY, BASE_URL, api_version="v2")

# Test connection and get account info
account = api.get_account()
print(f"Account ID: {account.id}")
print(f"Account status: {account.status}")
print(f"Cash balance: ${float(account.cash):,.2f}")
print(f"Portfolio value: ${float(account.portfolio_value):,.2f}")
print(f"Buying power: ${float(account.buying_power):,.2f}")
print(f"Trading mode: {'PAPER' if PAPER else 'LIVE'}")
