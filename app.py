from flask import Flask, request, jsonify, render_template_string
from flask_cors import CORS
import os
import openai
import json
from datetime import datetime
import re
import threading
import time
import imaplib
import smtplib
import email
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import ssl
import logging

app = Flask(__name__)
CORS(app)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize OpenAI
openai.api_key = os.getenv('OPENAI_API_KEY')

# OTL Configuration
OTL_CONFIG = {
    "organization": "Outreach & Transform Lives (OTL)",
    "mission": "Empowering East African immigrant communities",
    "email_accounts": [
        "info@outreachandtransformlives.org",
        "mdigo@outreachandtransformlives.org", 
        "jasmine@outreachandtransformlives.org",
        "admin@outreachandtransformlives.org",
        "Carole@outreachandtransformlives.org",
        "info@emdigo.org",
        "mdigo@emdigo.org"
    ],
    "vip_domains": [
        "gatesfoundation.org",
        "fordfoundation.org",
        "seattle.gov",
        "king-county.gov",
        "wa.gov",
        "hud.gov",
        "state.gov"
    ],
    "programs": [
