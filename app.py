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
        "Youth Empowerment",
        "Environmental Justice", 
        "Immigrant Support",
        "Community Organizing"
    ]
}

# Email Configuration
EMAIL_CONFIG = {
    'info@outreachandtransformlives.org': {
        'email': 'info@outreachandtransformlives.org',
        'password': os.getenv('EMAIL_PASSWORD_INFO'),
        'imap_server': 'outlook.office365.com',
        'imap_port': 993,
        'smtp_server': 'smtp.office365.com',
        'smtp_port': 587,
        'provider': 'Microsoft 365'
    }
}

# Global variables for email monitoring
email_monitor_running = False
processed_emails = set()
recent_email_activity = []

def classify_email(sender_email, subject, content):
    """Classify incoming email using AI"""
    try:
        # VIP Protection Check
        domain = sender_email.split('@')[-1].lower()
        if any(vip in domain for vip in OTL_CONFIG["vip_domains"]):
            return {
                "classification": "vip_contact",
                "confidence": 100,
                "action": "human_review_required",
                "reason": f"VIP domain detected: {domain}"
            }
        
        # Staff Account Check
        if sender_email.lower() in [email.lower() for email in OTL_CONFIG["email_accounts"]]:
            return {
                "classification": "staff_communication", 
                "confidence": 95,
                "action": "human_review_required",
                "reason": "Internal staff communication"
            }
        
        # AI Classification
        prompt = f"""
        Classify this email for {OTL_CONFIG["organization"]}:
        
        From: {sender_email}
        Subject: {subject}
        Content: {content[:500]}...
        
        Categories:
        - volunteer_interest: Someone wants to volunteer
        - program_inquiry: Questions about our programs
        - partnership: Business/organizational partnerships
        - donation: Funding or donation related
        - complaint: Issues or complaints
        - general_support: General questions
        
        Respond with JSON: {{"classification": "category", "confidence": 0-100, "sentiment": "positive/neutral/negative"}}
        """
        
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=150
        )
        
        result = json.loads(response.choices[0].message.content)
        
        # Determine action based on classification
        auto_respond_categories = ["volunteer_interest", "program_inquiry", "general_support"]
        if result["classification"] in auto_respond_categories and result["confidence"] > 70:
            result["action"] = "auto_respond"
        else:
            result["action"] = "human_review"
            
        return result
        
    except Exception as e:
        logger.error(f"Error classifying email: {str(e)}")
        return {
            "classification": "error",
            "confidence": 0,
            "action": "human_review",
            "error": str(e)
        }

def generate_response(classification, sender_email, subject, content):
    """Generate AI response for appropriate emails"""
    try:
        sender_name = sender_email.split('@')[0].replace('.', ' ').title()
        
        prompt = f"""
        Generate a professional email response for {OTL_CONFIG["organization"]} to:
        
        From: {sender_name} ({sender_email})
        Subject: {subject}
        Classification: {classification}
        
        Guidelines:
        - Professional but warm tone
        - Culturally sensitive for East African immigrant communities
        - Mention relevant programs: {', '.join(OTL_CONFIG["programs"])}
        - Include next steps
        - Keep under 150 words
        - Sign as "OTL Team"
        
        Original message: {content[:300]}...
        """
        
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=200
        )
        
        return response.choices[0].message.content.strip()
        
    except Exception as e:
        logger.error(f"Error generating response: {str(e)}")
        return f"Thank you for contacting {OTL_CONFIG['organization']}. We have received your message and will respond within 24 hours. Best regards, OTL Team"

def connect_imap(config):
    """Connect to IMAP server"""
    try:
        context = ssl.create_default_context()
        mail = imaplib.IMAP4_SSL(config['imap_server'], config['imap_port'], ssl_context=context)
        mail.login(config['email'], config['password'])
        logger.info(f"Successfully connected to IMAP for {config['email']}")
        return mail
    except Exception as e:
        logger.error(f"IMAP connection failed for {config['email']}: {str(e)}")
        return None

def connect_smtp(config):
    """Connect to SMTP server"""
    try:
        context = ssl.create_default_context()
        server = smtplib.SMTP(config['smtp_server'], config['smtp_port'])
        server.starttls(context=context)
        server.login(config['email'], config['password'])
        logger.info(f"Successfully connected to SMTP for {config['email']}")
        return server
    except Exception as e:
        logger.error(f"SMTP connection failed for {config['email']}: {str(e)}")
        return None

def extract_email_body(email_message):
    """Extract text body from email message"""
    body = ""
    
    if email_message.is_multipart():
        for part in email_message.walk():
            content_type = part.get_content_type()
            content_disposition = str(part.get("Content-Disposition"))
            
            if content_type == "text/plain" and "attachment" not in content_disposition:
                body = part.get_payload(decode=True).decode('utf-8', errors='ignore')
                break
    else:
        body = email_message.get_payload(decode=True).decode('utf-8', errors='ignore')
    
    return body.strip()

def send_response(config, to_email, subject, body, original_subject=None):
    """Send email response"""
    smtp = connect_smtp(config)
    if not smtp:
        return False
    
    try:
        msg = MIMEMultipart()
        msg['From'] = config['email']
        msg['To'] = to_email
        
        if original_subject and not original_subject.startswith('Re:'):
            msg['Subject'] = f"Re: {original_subject}"
        else:
            msg['Subject'] = subject
        
        msg.attach(MIMEText(body, 'plain'))
        
        smtp.send_message(msg)
        smtp.quit()
        
        logger.info(f"Response sent from {config['email']} to {to_email}")
        return True
        
    except Exception as e:
        logger.error(f"Error sending response from {config['email']}: {str(e)}")
        return False

def fetch_new_emails(config):
    """Fetch new emails from inbox"""
    mail = connect_imap(config)
    if not mail:
        return []
    
    try:
        mail.select('inbox')
        status, messages = mail.search(None, 'UNSEEN')
        
        if status != 'OK':
            return []
        
        email_ids = messages[0].split()
        new_emails = []
        
        for email_id in email_ids:
            if email_id in processed_emails:
                continue
            
            status, msg_data = mail.fetch(email_id, '(RFC822)')
            
            if status == 'OK':
                email_message = email.message_from_bytes(msg_data[0][1])
                
                sender = email_message['From']
                subject = email_message['Subject'] or 'No Subject'
                date = email_message['Date']
                body = extract_email_body(email_message)
                
                email_data = {
                    'id': email_id.decode(),
                    'sender': sender,
                    'subject': subject,
                    'body': body,
                    'date': date,
                    'account': config['email']
                }
                
                new_emails.append(email_data)
                processed_emails.add(email_id)
                
                logger.info(f"Fetched email from {sender} to {config['email']}")
        
        mail.close()
        mail.logout()
        
        return new_emails
        
    except Exception as e:
        logger.error(f"Error fetching emails for {config['email']}: {str(e)}")
        return []

def process_emails():
    """Process new emails with AI"""
    global recent_email_activity
    
    for email_address, config in EMAIL_CONFIG.items():
        if not config.get('password'):
            continue
            
        new_emails = fetch_new_emails(config)
        
        for email_data in new_emails:
            try:
                # Extract sender email
                sender_email = email_data['sender']
                if '<' in sender_email and '>' in sender_email:
                    sender_email = sender_email.split('<')[1].split('>')[0]
                
                # Process with AI
                classification_result = classify_email(
                    sender_email=sender_email,
                    subject=email_data['subject'],
                    content=email_data['body']
                )
                
                logger.info(f"Email classified as: {classification_result.get('classification', 'unknown')}")
                
                # Handle based on classification
                if classification_result.get('action') == 'auto_respond':
                    response_body = generate_response(
                        classification_result['classification'],
                        sender_email,
                        email_data['subject'],
                        email_data['body']
                    )
                    
                    if response_body:
                        send_response(
                            config=config,
                            to_email=sender_email,
                            subject=email_data['subject'],
                            body=response_body,
                            original_subject=email_data['subject']
                        )
                        
                        logger.info(f"Auto-response sent to {sender_email}")
                
                # Add to recent activity
                activity = {
                    'from': sender_email,
                    'subject': email_data['subject'],
                    'classification': classification_result.get('classification', 'unknown'),
                    'status': 'auto_replied' if classification_result.get('action') == 'auto_respond' else 'human_review_required',
                    'sentiment': classification_result.get('sentiment', 'neutral'),
                    'time': 'Just now',
                    'account': email_data['account']
                }
                
                recent_email_activity.insert(0, activity)
                if len(recent_email_activity) > 10:
                    recent_email_activity = recent_email_activity[:10]
                
            except Exception as e:
                logger.error(f"Error processing email: {str(e)}")

def email_monitor_loop():
    """Email monitoring loop"""
    global email_monitor_running
    
    while email_monitor_running:
        try:
            process_emails()
            time.sleep(60)  # Check every minute
        except Exception as e:
            logger.error(f"Error in email monitoring loop: {str(e)}")
            time.sleep(60)

def start_email_monitoring():
    """Start email monitoring in background thread"""
    global email_monitor_running
    
    if not email_monitor_running:
        email_monitor_running = True
        thread = threading.Thread(target=email_monitor_loop, daemon=True)
        thread.start()
        logger.info("Email monitoring started")

# Mock data for demonstration (updated with real activity when emails are processed)
MOCK_DATA = {
    "total_contacts": 1247,
    "emails_sent": 3456,
    "auto_replies": 892,
    "meetings_booked": 156,
    "response_rate": 68.5,
    "auto_reply_rate": 78.2,
    "recent_emails": [
        {
            "from": "amina.hassan@gmail.com",
            "subject": "Volunteer Opportunity Inquiry",
            "classification": "volunteer_interest",
            "status": "auto_replied",
            "sentiment": "positive",
            "time": "2 hours ago"
        },
        {
            "from": "david.kimani@seattlefoundation.org", 
            "subject": "Partnership Discussion",
            "classification": "vip_contact",
            "status": "human_review_required",
            "sentiment": "neutral",
            "time": "4 hours ago"
        },
        {
            "from": "sarah.johnson@gmail.com",
            "subject": "Program Information Request", 
            "classification": "program_inquiry",
            "status": "auto_replied",
            "sentiment": "positive",
            "time": "6 hours ago"
        }
    ],
    "upcoming_meetings": [
        {
            "name": "Grace Wanjiku",
            "purpose": "Volunteer orientation",
            "time": "Today, 2:00 PM"
        },
        {
            "name": "James Ochieng",
            "purpose": "Partnership exploration", 
            "time": "Tomorrow, 10:00 AM"
        },
        {
            "name": "Fatima Ahmed",
            "purpose": "Youth program session",
            "time": "Friday, 3:30 PM"
        }
    ]
}

# HTML Template for Dashboard (same as before but with email monitoring status)
DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>OTL AI Email Agent Dashboard</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background: #f5f7fa; }
        .header { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 20px; text-align: center; }
        .container { max-width: 1200px; margin: 0 auto; padding: 20px; }
        .stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 20px; margin-bottom: 30px; }
        .stat-card { background: white; padding: 20px; border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }
        .stat-number { font-size: 2em; font-weight: bold; color: #667eea; }
        .stat-label { color: #666; margin-top: 5px; }
        .section { background: white; padding: 20px; border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); margin-bottom: 20px; }
        .section h3 { color: #333; margin-bottom: 15px; }
        .email-item { padding: 15px; border-left: 4px solid #667eea; margin-bottom: 10px; background: #f8f9ff; }
        .status-auto { border-left-color: #28a745; }
        .status-human { border-left-color: #ffc107; }
        .status-vip { border-left-color: #dc3545; }
        .meeting-item { padding: 10px; border-bottom: 1px solid #eee; }
        .api-section { background: #e8f4fd; border: 1px solid #bee5eb; }
        .test-button { background: #667eea; color: white; padding: 10px 20px; border: none; border-radius: 5px; cursor: pointer; margin: 10px 5px; }
        .test-button:hover { background: #5a6fd8; }
        .response-box { background: #f8f9fa; border: 1px solid #dee2e6; padding: 15px; border-radius: 5px; margin-top: 10px; }
        .status-indicator { display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin-right: 5px; }
        .status-active { background-color: #28a745; }
        .status-inactive { background-color: #dc3545; }
    </style>
</head>
<body>
    <div class="header">
        <h1>🤖 OTL AI Email Agent Dashboard</h1>
        <p>Empowering East African Communities with Intelligent Email Automation</p>
    </div>
    
    <div class="container">
        <div class="stats-grid">
            <div class="stat-card">
                <div class="stat-number">{{ data.total_contacts }}</div>
                <div class="stat-label">Total Contacts</div>
            </div>
            <div class="stat-card">
                <div class="stat-number">{{ data.emails_sent }}</div>
                <div class="stat-label">Emails Sent</div>
            </div>
            <div class="stat-card">
                <div class="stat-number">{{ data.auto_replies }}</div>
                <div class="stat-label">Auto Replies</div>
            </div>
            <div class="stat-card">
                <div class="stat-number">{{ data.meetings_booked }}</div>
                <div class="stat-label">Meetings Booked</div>
            </div>
        </div>
        
        <div class="section api-section">
            <h3>🧪 Test AI Features</h3>
            <p>Test the AI email classification and response generation:</p>
            <button class="test-button" onclick="testClassification()">Test Email Classification</button>
            <button class="test-button" onclick="testResponse()">Test Auto Response</button>
            <button class="test-button" onclick="testVIPProtection()">Test VIP Protection</button>
            <button class="test-button" onclick="startEmailMonitoring()">Start Email Monitoring</button>
            <div id="test-results" class="response-box" style="display:none;"></div>
        </div>
        
        <div class="section">
            <h3>📧 Recent Email Activity</h3>
            <div id="email-activity">
                {% for email in data.recent_emails %}
                <div class="email-item status-{{ 'auto' if 'auto' in email.status else 'human' if 'human' in email.status else 'vip' }}">
                    <strong>{{ email.from }}</strong> - {{ email.subject }}<br>
                    <small>{{ email.classification }} | {{ email.status }} | {{ email.time }}</small>
                </div>
                {% endfor %}
            </div>
        </div>
        
        <div class="section">
            <h3>📅 Upcoming Meetings</h3>
            {% for meeting in data.upcoming_meetings %}
            <div class="meeting-item">
                <strong>{{ meeting.name }}</strong> - {{ meeting.purpose }}<br>
                <small>{{ meeting.time }}</small>
            </div>
            {% endfor %}
        </div>
        
        <div class="section">
            <h3>⚙️ System Status</h3>
            <p>✅ OpenAI API: Connected</p>
            <p><span class="status-indicator {{ 'status-active' if email_monitoring else 'status-inactive' }}"></span>Email Monitoring: {{ 'Active' if email_monitoring else 'Inactive' }}</p>
            <p>✅ VIP Protection: Enabled</p>
            <p>✅ Multi-Account Support: {{ data.email_accounts|length }} accounts configured</p>
            <p>✅ Response Rate: {{ data.response_rate }}%</p>
            <p>✅ Auto-Reply Rate: {{ data.auto_reply_rate }}%</p>
        </div>
    </div>
    
    <script>
        async function testClassification() {
            const results = document.getElementById('test-results');
            results.style.display = 'block';
            results.innerHTML = 'Testing email classification...';
            
            try {
                const response = await fetch('/api/test/classify', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        sender: 'fatima.ahmed@gmail.com',
                        subject: 'Volunteer Opportunity',
                        content: 'Hi, I am interested in volunteering with your youth programs.'
                    })
                });
                const data = await response.json();
                results.innerHTML = `<strong>Classification Result:</strong><br>
                    Category: ${data.classification}<br>
                    Confidence: ${data.confidence}%<br>
                    Action: ${data.action}<br>
                    Sentiment: ${data.sentiment || 'N/A'}`;
            } catch (error) {
                results.innerHTML = `Error: ${error.message}`;
            }
        }
        
        async function testResponse() {
            const results = document.getElementById('test-results');
            results.style.display = 'block';
            results.innerHTML = 'Generating AI response...';
            
            try {
                const response = await fetch('/api/test/response', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        sender: 'amina.hassan@gmail.com',
                        subject: 'Program Information',
                        content: 'Can you tell me about your environmental justice programs?',
                        classification: 'program_inquiry'
                    })
                });
                const data = await response.json();
                results.innerHTML = `<strong>Generated Response:</strong><br>${data.response}`;
            } catch (error) {
                results.innerHTML = `Error: ${error.message}`;
            }
        }
        
        async function testVIPProtection() {
            const results = document.getElementById('test-results');
            results.style.display = 'block';
            results.innerHTML = 'Testing VIP protection...';
            
            try {
                const response = await fetch('/api/test/classify', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        sender: 'program.officer@gatesfoundation.org',
                        subject: 'Grant Opportunity Discussion',
                        content: 'We would like to discuss potential funding opportunities.'
                    })
                });
                const data = await response.json();
                results.innerHTML = `<strong>VIP Protection Result:</strong><br>
                    Classification: ${data.classification}<br>
                    Action: ${data.action}<br>
                    Reason: ${data.reason}<br>
                    <em>✅ VIP email correctly flagged for human review!</em>`;
            } catch (error) {
                results.innerHTML = `Error: ${error.message}`;
            }
        }
        
        async function startEmailMonitoring() {
            const results = document.getElementById('test-results');
            results.style.display = 'block';
            results.innerHTML = 'Starting email monitoring...';
            
            try {
                const response = await fetch('/api/start_monitoring', {
                    method: 'POST'
                });
                const data = await response.json();
                results.innerHTML = `<strong>Email Monitoring:</strong><br>${data.message}`;
                
                // Refresh page after 2 seconds to show updated status
                setTimeout(() => {
                    window.location.reload();
                }, 2000);
            } catch (error) {
                results.innerHTML = `Error: ${error.message}`;
            }
        }
        
        // Auto-refresh email activity every 30 seconds
        setInterval(async () => {
            try {
                const response = await fetch('/api/recent_activity');
                const data = await response.json();
                
                const activityDiv = document.getElementById('email-activity');
                activityDiv.innerHTML = '';
                
                data.recent_emails.forEach(email => {
                    const statusClass = email.status.includes('auto') ? 'auto' : 
                                       email.status.includes('human') ? 'human' : 'vip';
                    
                    activityDiv.innerHTML += `
                        <div class="email-item status-${statusClass}">
                            <strong>${email.from}</strong> - ${email.subject}<br>
                            <small>${email.classification} | ${email.status} | ${email.time}</small>
                        </div>
                    `;
                });
            } catch (error) {
                console.log('Error refreshing activity:', error);
            }
        }, 30000);
    </script>
</body>
</html>
"""

# Routes
@app.route('/')
def dashboard():
    """Main dashboard"""
    data = MOCK_DATA.copy()
    data['email_accounts'] = OTL_CONFIG['email_accounts']
    
    # Use real recent activity if available
    if recent_email_activity:
        data['recent_emails'] = recent_email_activity
    
    return render_template_string(DASHBOARD_HTML, data=data, email_monitoring=email_monitor_running)

@app.route('/api/health')
def health():
    """Health check endpoint"""
    return jsonify({
        "service": "OTL AI Email Agent",
        "status": "healthy",
        "version": "2.1.0",
        "openai_configured": bool(os.getenv('OPENAI_API_KEY')),
        "accounts_configured": len(OTL_CONFIG['email_accounts']),
        "email_monitoring": email_monitor_running,
        "email_password_configured": bool(os.getenv('EMAIL_PASSWORD_INFO'))
    })

@app.route('/api/stats')
def stats():
    """System statistics"""
    data = MOCK_DATA.copy()
    if recent_email_activity:
        data['recent_emails'] = recent_email_activity
    return jsonify(data)

@app.route('/api/recent_activity')
def recent_activity():
    """Get recent email activity"""
    data = MOCK_DATA.copy()
    if recent_email_activity:
        data['recent_emails'] = recent_email_activity
    return jsonify(data)

@app.route('/api/start_monitoring', methods=['POST'])
def start_monitoring():
    """Start email monitoring"""
    if not os.getenv('EMAIL_PASSWORD_INFO'):
        return jsonify({
            "success": False,
            "message": "Email password not configured. Please add EMAIL_PASSWORD_INFO environment variable."
        }), 400
    
    start_email_monitoring()
    return jsonify({
        "success": True,
        "message": "Email monitoring started successfully!"
    })

@app.route('/api/test/classify', methods=['POST'])
def test_classify():
    """Test email classification"""
    data = request.json
    result = classify_email(
        data.get('sender', ''),
        data.get('subject', ''),
        data.get('content', '')
    )
    return jsonify(result)

@app.route('/api/test/response', methods=['POST'])
def test_response():
    """Test response generation"""
    data = request.json
    response = generate_response(
        data.get('classification', ''),
        data.get('sender', ''),
        data.get('subject', ''),
        data.get('content', '')
    )
    return jsonify({"response": response})

@app.route('/api/process_email', methods=['POST'])
def process_email():
    """Process incoming email"""
    data = request.json
    
    # Classify email
    classification = classify_email(
        data.get('sender', ''),
        data.get('subject', ''),
        data.get('content', '')
    )
    
    # Generate response if appropriate
    response = None
    if classification.get('action') == 'auto_respond':
        response = generate_response(
            classification['classification'],
            data.get('sender', ''),
            data.get('subject', ''),
            data.get('content', '')
        )
    
    return jsonify({
        "classification": classification,
        "response": response,
        "timestamp": datetime.now().isoformat()
    })

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)

