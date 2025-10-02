import os
import json
from flask import Flask, request, redirect, session, url_for, render_template
from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

# --- Main Application Setup ---
app = Flask(__name__)
# This secret key is needed to sign the session cookie
app.secret_key = 'c72b8a032884a0c86237f8228fb2179836103842c2f883f3' # Replace with a real secret key
os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1' # Allows http for local testing

# --- Google OAuth2.0 Setup ---
CLIENT_SECRETS_FILE = "client_secret.json"
# Drive scope - Google automatically adds email and openid scopes
SCOPES = [
    'https://www.googleapis.com/auth/drive',
    'https://www.googleapis.com/auth/userinfo.email', 
    'openid'
]

# --- Drive Copy Logic (Adapted from main.py) ---
import json
import time
from datetime import datetime
from googleapiclient.errors import HttpError

PROGRESS_FILE = 'copy_progress.json'
FAILED_LOG_FILE = 'failed_files.log'

def load_progress():
    """Loads the progress from a JSON file."""
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE, 'r') as f:
            return json.load(f)
    return {'folder_map': {}, 'copied_files': []}

def save_progress(progress):
    """Saves the current progress to a JSON file."""
    with open(PROGRESS_FILE, 'w') as f:
        json.dump(progress, f, indent=4)

def log_failure(path, error):
    """Logs a failed file or folder operation to the log file."""
    with open(FAILED_LOG_FILE, 'a') as f:
        f.write(f"[{datetime.now().isoformat()}] Path: {path} | Error: {str(error)}\n")

def extract_folder_id(url_or_id):
    """Extract folder ID from Google Drive URL or return the ID if already provided."""
    if 'drive.google.com' in url_or_id:
        # Extract ID from URL like: https://drive.google.com/drive/folders/1ABC...XYZ
        if '/folders/' in url_or_id:
            return url_or_id.split('/folders/')[1].split('?')[0].split('/')[0]
    return url_or_id.strip()

def copy_folder_contents(service, source_folder_id, dest_folder_id, progress, path=""):
    """Recursively copies files and folders with progress tracking."""
    if source_folder_id not in progress['folder_map']:
        progress['folder_map'][source_folder_id] = dest_folder_id
        save_progress(progress)
    
    page_token = None
    while True:
        try:
            response = service.files().list(
                q=f"'{source_folder_id}' in parents and trashed=false",
                fields="nextPageToken, files(id, name, mimeType)",
                pageToken=page_token,
                pageSize=100
            ).execute()
        except HttpError as e:
            print(f"ERROR: Could not list files in folder ID '{source_folder_id}': {e}")
            log_failure(f"{path}/<folder_listing_failed>", e)
            return

        items = response.get('files', [])
        
        for item in items:
            item_id = item['id']
            item_name = item['name']
            item_type = item['mimeType']
            current_path = os.path.join(path, item_name)

            if item_type == 'application/vnd.google-apps.folder':
                # Handle folders
                new_dest_folder_id = None
                if item_id in progress['folder_map']:
                    print(f"SKIPPING: Folder already exists - {current_path}")
                    new_dest_folder_id = progress['folder_map'][item_id]
                else:
                    print(f"CREATING: Folder - {current_path}")
                    folder_metadata = {
                        'name': item_name,
                        'mimeType': 'application/vnd.google-apps.folder',
                        'parents': [dest_folder_id]
                    }
                    try:
                        new_folder = service.files().create(body=folder_metadata, fields='id').execute()
                        new_dest_folder_id = new_folder['id']
                        progress['folder_map'][item_id] = new_dest_folder_id
                        save_progress(progress)
                    except HttpError as e:
                        print(f"ERROR: Failed to create folder '{current_path}': {e}")
                        log_failure(current_path, e)
                        continue
                
                copy_folder_contents(service, item_id, new_dest_folder_id, progress, current_path)

            else:
                # Handle files
                if item_id in progress['copied_files']:
                    print(f"SKIPPING: File already copied - {current_path}")
                    continue

                print(f"COPYING: File - {current_path}")
                file_metadata = {'parents': [dest_folder_id]}
                
                retries = 3
                for i in range(retries):
                    try:
                        service.files().copy(fileId=item_id, body=file_metadata).execute()
                        progress['copied_files'].append(item_id)
                        save_progress(progress)
                        print(f"SUCCESS: Copied - {current_path}")
                        break
                    except HttpError as e:
                        error_reason = json.loads(e.content).get('error', {}).get('errors', [{}])[0].get('reason', 'unknown')
                        if error_reason in ['userRateLimitExceeded', 'rateLimitExceeded']:
                            wait_time = (2 ** i) + 1
                            print(f"RATE LIMIT: Waiting {wait_time}s before retry ({i+1}/{retries})")
                            time.sleep(wait_time)
                        elif error_reason == 'cannotCopyFile':
                            print(f"SKIPPED: File not copyable - {current_path}")
                            log_failure(current_path, f"Permission error: {error_reason}")
                            break
                        else:
                            print(f"ERROR: Failed to copy file '{current_path}': {e}")
                            log_failure(current_path, e)
                            break

        page_token = response.get('nextPageToken')
        if not page_token:
            break


# --- Flask Routes ---

@app.route('/')
def index():
    try:
        print("Index route called")  # Debug output
        # If a user's credentials are not in the session, show the login page
        if 'credentials' not in session:
            print("User not logged in")  # Debug output
            return render_template('index.html', logged_in=False)
        
        # If the user is logged in, show the main application page
        print("User logged in")  # Debug output
        return render_template('index.html', logged_in=True)
    except Exception as e:
        print(f"Error in index route: {e}")  # Debug output
        return f"Template Error: {e}"

@app.route('/login')
def login():
    try:
        print("OAUTH: Starting authentication process...")
        # Create a Flow instance to manage the OAuth 2.0 Authorization Grant Flow.
        flow = Flow.from_client_secrets_file(
            CLIENT_SECRETS_FILE,
            scopes=SCOPES,
            redirect_uri=url_for('callback', _external=True)
        )
        print(f"OAUTH: Redirect URI configured - {url_for('callback', _external=True)}")
        
        # Generate the URL that the user will be sent to for authorization.
        authorization_url, state = flow.authorization_url(
            access_type='offline', 
            include_granted_scopes='true'
        )
        
        # Store the state in the session so we can verify it in the callback
        session['state'] = state
        print(f"OAUTH: Generated authorization URL - {authorization_url}")
        print(f"OAUTH: Redirecting user to Google authentication...")
        
        return redirect(authorization_url)
    except Exception as e:
        print(f"OAUTH ERROR: Login failed - {e}")
        return f"Login Error: {str(e)}", 500

@app.route('/callback')
def callback():
    try:
        print("OAUTH: Processing authentication callback...")
        
        # Check if we have a state in session
        if 'state' not in session:
            print("OAUTH ERROR: No state found in session")
            return "OAuth Error: No state found in session", 400
            
        # Verify that the state from the session matches the state from the request.
        state = session['state']
        
        # Check for error in the callback
        if 'error' in request.args:
            error = request.args.get('error')
            print(f"OAUTH ERROR: Authentication failed - {error}")
            return f"OAuth Error: {error}", 400
        
        flow = Flow.from_client_secrets_file(
            CLIENT_SECRETS_FILE,
            scopes=SCOPES,
            state=state,
            redirect_uri=url_for('callback', _external=True)
        )

        # Use the authorization server's response to fetch the OAuth 2.0 tokens.
        authorization_response = request.url
        print(f"OAUTH: Processing authorization response - {authorization_response}")
        
        flow.fetch_token(authorization_response=authorization_response)
        print("OAUTH: Successfully obtained access tokens")

        # Store the credentials in the session.
        credentials = flow.credentials
        session['credentials'] = {
            'token': credentials.token,
            'refresh_token': credentials.refresh_token,
            'token_uri': credentials.token_uri,
            'client_id': credentials.client_id,
            'client_secret': credentials.client_secret,
            'scopes': credentials.scopes
        }
        
        print("OAUTH: Authentication completed successfully")
        return redirect(url_for('index'))
        
    except Exception as e:
        print(f"OAUTH ERROR: Callback processing failed - {e}")
        return f"OAuth Callback Error: {str(e)}", 500

@app.route('/logout')
def logout():
    session.clear()  # Clear entire session including credentials and state
    return redirect(url_for('index'))

@app.route('/force-reauth')
def force_reauth():
    """Force complete re-authentication with new scopes"""
    session.clear()
    return f"""
    <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 20px auto; padding: 20px; background: #fff3cd; border-radius: 10px;">
        <h2>🔄 Re-authentication Required</h2>
        <p>Your session has been cleared. Please log in again to get Drive permissions.</p>
        <div style="text-align: center; margin-top: 20px;">
            <a href="/login" style="background: #4285f4; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px;">Login with Drive Permissions</a>
        </div>
        <div style="margin-top: 15px; padding: 10px; background: #e3f2fd; border-radius: 5px; font-size: 14px;">
            <strong>Note:</strong> Make sure you've added the Drive scope (https://www.googleapis.com/auth/drive) in your Google Cloud Console OAuth consent screen.
        </div>
    </div>
    """

@app.route('/privacy')
def privacy():
    """Privacy Policy page required for Google OAuth verification"""
    return """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Privacy Policy - Drive Forker</title>
        <style>
            body { font-family: Arial, sans-serif; max-width: 800px; margin: 0 auto; padding: 20px; line-height: 1.6; }
            h1, h2 { color: #333; }
            .back-link { display: inline-block; margin-bottom: 20px; color: #4285f4; text-decoration: none; }
        </style>
    </head>
    <body>
        <a href="/" class="back-link">← Back to Drive Forker</a>
        <h1>Privacy Policy</h1>
        <p><strong>Last updated:</strong> September 23, 2025</p>
        
        <h2>Information We Collect</h2>
        <p>Drive Forker ("we", "our", or "us") collects and processes the following information:</p>
        <ul>
            <li><strong>Google Account Information:</strong> Your email address and basic profile information when you sign in with Google</li>
            <li><strong>Google Drive Access:</strong> We access your Google Drive files only to perform the copying operations you request</li>
            <li><strong>Usage Data:</strong> Basic application logs for debugging and performance monitoring</li>
        </ul>
        
        <h2>How We Use Your Information</h2>
        <p>We use your information solely to:</p>
        <ul>
            <li>Authenticate you with Google's OAuth system</li>
            <li>Access your Google Drive to copy folders as requested</li>
            <li>Provide the core functionality of the Drive Forker service</li>
        </ul>
        
        <h2>Data Storage and Security</h2>
        <ul>
            <li>We do not permanently store your Google Drive files</li>
            <li>Authentication tokens are stored temporarily during your session</li>
            <li>All data transmission is encrypted using HTTPS</li>
            <li>We do not sell, trade, or share your personal information with third parties</li>
        </ul>
        
        <h2>Third-Party Services</h2>
        <p>This application uses Google's APIs and is subject to Google's Privacy Policy. We only request the minimum permissions necessary to provide our service.</p>
        
        <h2>Your Rights</h2>
        <p>You can:</p>
        <ul>
            <li>Revoke access to your Google account at any time through your Google Account settings</li>
            <li>Log out of the application to clear your session data</li>
            <li>Contact us with any privacy concerns</li>
        </ul>
        
        <h2>Contact Information</h2>
        <p>If you have any questions about this Privacy Policy, please contact us at: [Your Email Address]</p>
        
        <h2>Changes to This Policy</h2>
        <p>We may update this Privacy Policy from time to time. We will notify users of any significant changes by posting the new Privacy Policy on this page.</p>
    </body>
    </html>
    """

@app.route('/terms')
def terms():
    """Terms of Service page required for Google OAuth verification"""
    return """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Terms of Service - Drive Forker</title>
        <style>
            body { font-family: Arial, sans-serif; max-width: 800px; margin: 0 auto; padding: 20px; line-height: 1.6; }
            h1, h2 { color: #333; }
            .back-link { display: inline-block; margin-bottom: 20px; color: #4285f4; text-decoration: none; }
        </style>
    </head>
    <body>
        <a href="/" class="back-link">← Back to Drive Forker</a>
        <h1>Terms of Service</h1>
        <p><strong>Last updated:</strong> September 23, 2025</p>
        
        <h2>1. Acceptance of Terms</h2>
        <p>By using Drive Forker, you agree to these Terms of Service. If you do not agree to these terms, please do not use our service.</p>
        
        <h2>2. Description of Service</h2>
        <p>Drive Forker is a web application that allows users to copy entire Google Drive folders from one location to another within their Google Drive account or to accounts they have permission to access.</p>
        
        <h2>3. User Responsibilities</h2>
        <p>You agree to:</p>
        <ul>
            <li>Use the service only for legitimate purposes</li>
            <li>Respect intellectual property rights and copyright laws</li>
            <li>Not copy files you don't have permission to access</li>
            <li>Not use the service to violate any applicable laws or regulations</li>
            <li>Not attempt to reverse engineer or compromise the security of the service</li>
        </ul>
        
        <h2>4. Limitations and Disclaimers</h2>
        <ul>
            <li>The service is provided "as is" without warranties of any kind</li>
            <li>We are not responsible for any data loss or corruption</li>
            <li>We do not guarantee uninterrupted or error-free service</li>
            <li>Users are responsible for backing up their important data</li>
        </ul>
        
        <h2>5. Privacy and Data Protection</h2>
        <p>Your privacy is important to us. Please review our Privacy Policy to understand how we collect, use, and protect your information.</p>
        
        <h2>6. Google Drive Integration</h2>
        <p>This service integrates with Google Drive through official Google APIs. You must comply with Google's Terms of Service when using this application.</p>
        
        <h2>7. Termination</h2>
        <p>We reserve the right to terminate or suspend access to our service at any time, without prior notice, for conduct that we believe violates these Terms of Service.</p>
        
        <h2>8. Changes to Terms</h2>
        <p>We reserve the right to modify these terms at any time. Changes will be effective immediately upon posting to this page.</p>
        
        <h2>9. Contact Information</h2>
        <p>If you have any questions about these Terms of Service, please contact us at: [Your Email Address]</p>
        
        <h2>10. Governing Law</h2>
        <p>These terms shall be governed by and construed in accordance with applicable laws.</p>
    </body>
    </html>
    """

@app.route('/debug-oauth')
def debug_oauth():
    """Debug route to test OAuth URL generation"""
    try:
        flow = Flow.from_client_secrets_file(
            CLIENT_SECRETS_FILE,
            scopes=SCOPES,
            redirect_uri=url_for('callback', _external=True)
        )
        
        authorization_url, state = flow.authorization_url(
            access_type='offline', 
            include_granted_scopes='true'
        )
        
        return f"""
        <div style="font-family: Arial, sans-serif; padding: 20px;">
            <h2>OAuth Debug Information</h2>
            <p><strong>Client ID:</strong> {flow.client_config['client_id']}</p>
            <p><strong>Redirect URI:</strong> {url_for('callback', _external=True)}</p>
            <p><strong>Scopes:</strong> {', '.join(SCOPES)}</p>
            <p><strong>Authorization URL:</strong></p>
            <textarea style="width: 100%; height: 100px;">{authorization_url}</textarea>
            <br><br>
            <a href="{authorization_url}" target="_blank">Test OAuth URL (opens in new tab)</a>
            <br><br>
            <a href="/">Back to Home</a>
        </div>
        """
    except Exception as e:
        return f"Debug Error: {str(e)}"

@app.route('/copy', methods=['POST'])
def copy():
    if 'credentials' not in session:
        return 'User not authenticated', 401

    try:
        # Recreate credentials object from the session data
        creds = Credentials(**session['credentials'])
        service = build('drive', 'v3', credentials=creds)

        source_id = extract_folder_id(request.form.get('source_id'))
        dest_id = extract_folder_id(request.form.get('dest_id'))
        
        if not source_id or not dest_id:
            return 'Both source and destination folder IDs are required', 400

        # Load progress and start copying
        progress = load_progress()
        
        print(f"\nCOPY OPERATION: Initiating folder replication...")
        print(f"SOURCE: {source_id}")
        print(f"DESTINATION: {dest_id}")
        
        # Run the copy operation
        copy_folder_contents(service, source_id, dest_id, progress)
        
        # Get summary stats
        copied_files_count = len(progress.get('copied_files', []))
        created_folders_count = len(progress.get('folder_map', {})) - 1
        
        result_html = f"""
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Operation Complete - Drive Forker</title>
            <style>
                body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif; background: #f8f9fa; margin: 0; padding: 20px; }}
                .container {{ max-width: 700px; margin: 50px auto; background: white; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); border: 1px solid #e9ecef; }}
                .header {{ background: #28a745; color: white; padding: 30px; border-radius: 8px 8px 0 0; text-align: center; }}
                .content {{ padding: 30px; }}
                .summary {{ background: #f8f9fa; padding: 20px; border-radius: 6px; margin: 20px 0; border-left: 4px solid #28a745; }}
                .details {{ background: #e3f2fd; padding: 20px; border-radius: 6px; margin: 20px 0; }}
                .warning {{ background: #fff3cd; padding: 20px; border-radius: 6px; margin: 20px 0; border-left: 4px solid #ffc107; }}
                .success {{ background: #d4edda; padding: 20px; border-radius: 6px; margin: 20px 0; border-left: 4px solid #28a745; }}
                .actions {{ text-align: center; margin-top: 30px; }}
                .btn {{ background: #4285f4; color: white; padding: 12px 24px; text-decoration: none; border-radius: 4px; font-weight: 500; display: inline-block; transition: all 0.2s ease; }}
                .btn:hover {{ background: #3367d6; transform: translateY(-1px); }}
                ul {{ list-style: none; padding: 0; }}
                li {{ padding: 8px 0; border-bottom: 1px solid #dee2e6; }}
                li:last-child {{ border-bottom: none; }}
                .metric {{ font-weight: 600; color: #495057; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h1>Operation Completed Successfully</h1>
                    <p>Folder replication process has finished</p>
                </div>
                <div class="content">
                    <div class="summary">
                        <h3>Operation Summary</h3>
                        <ul>
                            <li><span class="metric">Files Processed:</span> {copied_files_count}</li>
                            <li><span class="metric">Folders Created:</span> {created_folders_count}</li>
                        </ul>
                    </div>
                    
                    <div class="details">
                        <h3>Operation Details</h3>
                        <p><strong>Source Location:</strong> {source_id}</p>
                        <p><strong>Destination Location:</strong> {dest_id}</p>
                    </div>
                    
                    {'<div class="warning"><h3>Processing Notice</h3><p>Some items could not be processed due to permissions or other restrictions. Please review the error log for detailed information.</p></div>' if os.path.exists(FAILED_LOG_FILE) else '<div class="success"><h3>Complete Success</h3><p>All items have been successfully processed and copied to the destination location.</p></div>'}
                    
                    <div class="actions">
                        <a href="/" class="btn">Initiate New Operation</a>
                    </div>
                </div>
            </div>
        </body>
        </html>
        """
        
        return result_html

    except Exception as e:
        print(f"Copy operation failed: {e}")
        log_failure("COPY_OPERATION_FAILED", e)
        return f"""
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Operation Failed - Drive Forker</title>
            <style>
                body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif; background: #f8f9fa; margin: 0; padding: 20px; }}
                .container {{ max-width: 700px; margin: 50px auto; background: white; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); border: 1px solid #e9ecef; }}
                .header {{ background: #dc3545; color: white; padding: 30px; border-radius: 8px 8px 0 0; text-align: center; }}
                .content {{ padding: 30px; }}
                .error {{ background: #f8d7da; padding: 20px; border-radius: 6px; margin: 20px 0; border-left: 4px solid #dc3545; }}
                .actions {{ text-align: center; margin-top: 30px; }}
                .btn {{ background: #4285f4; color: white; padding: 12px 24px; text-decoration: none; border-radius: 4px; font-weight: 500; display: inline-block; transition: all 0.2s ease; }}
                .btn:hover {{ background: #3367d6; transform: translateY(-1px); }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h1>Operation Failed</h1>
                    <p>The folder replication process encountered an error</p>
                </div>
                <div class="content">
                    <div class="error">
                        <h3>Error Details</h3>
                        <p><strong>Error Message:</strong> {str(e)}</p>
                        <p>Please verify your folder permissions and try again. If the problem persists, contact system administrator.</p>
                    </div>
                    
                    <div class="actions">
                        <a href="/" class="btn">Return to Main Interface</a>
                    </div>
                </div>
            </div>
        </body>
        </html>
        """, 500

if __name__ == '__main__':
    # Allow access from other computers on your network
    # Use 0.0.0.0 to accept connections from any IP
    app.run('localhost', 5000, debug=True)