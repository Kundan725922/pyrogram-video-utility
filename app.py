import os
import re
import tempfile 
from functools import wraps # <-- NEW: For the decorator
from flask import Flask, request, Response, send_from_directory, send_file 
from pyrogram import Client
from pyrogram.errors import UserNotParticipant
from flask_cors import CORS 

# --- Configuration & Environment Variables ---
API_ID = 35172395
API_HASH = "3cb710c4a835a23eeb73112026d46686"
BOT_TOKEN = None 
SESSION_NAME = "streamer_session" 

app = Flask(__name__)
CORS(app) 

telegram_client = None
DOWNLOADED_FILES_TO_CLEANUP = [] 

# Security: Define the access token from an Environment Variable
SECRET_ACCESS_TOKEN = os.environ.get("SERVER_ACCESS_TOKEN") # Will be set on Render

# --- API KEY VALIDATION FUNCTION ---
def check_api_key(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # 1. Check for the token in the URL query string parameter 'token'
        user_token = request.args.get('token')
        
        # 2. Validate against the stored SECRET_ACCESS_TOKEN
        if not SECRET_ACCESS_TOKEN or user_token != SECRET_ACCESS_TOKEN:
            return {"error": "Access Denied. Invalid or missing access token."}, 401
        
        # 3. If correct, execute the original function (stream_video or download_video)
        return f(*args, **kwargs)
    return decorated_function

# --- PYROGRAM CLIENT STARTUP (Uses Session String for Cloud) ---
SESSION_STRING = os.environ.get("PYROGRAM_SESSION") 

if not app.debug or os.environ.get('WERKZEUG_RUN_MAIN') == 'true':
    try:
        if SESSION_STRING:
            client_session_identifier = SESSION_STRING
            print("Client starting using PYROGRAM_SESSION environment variable.")
        else:
            client_session_identifier = SESSION_NAME
            print(f"Client starting using local file: {SESSION_NAME}.session")
            
        telegram_client = Client(client_session_identifier, API_ID, API_HASH, bot_token=BOT_TOKEN)
        telegram_client.start()
        print("Pyrogram Client started successfully!")
    except Exception as e:
        print(f"Error starting Pyrogram client (FATAL): {e}")
# -------------------------------------------------------------------

@app.teardown_request
def cleanup_files(exception=None):
    """Deletes temporary downloaded files after the request has been served."""
    global DOWNLOADED_FILES_TO_CLEANUP
    for file_path in DOWNLOADED_FILES_TO_CLEANUP:
        try:
            os.remove(file_path)
            print(f"Cleaned up temp file: {file_path}")
        except OSError as e:
            print(f"Error cleaning up file {file_path}: {e}")
    DOWNLOADED_FILES_TO_CLEANUP = []

# --- Utility Functions (parse_link and serve_frontend remain the same) ---

@app.route('/')
def serve_frontend():
    return send_from_directory(os.getcwd(), 'telegram_vedio_streammer.html')

def parse_link(url):
    match = re.search(r't\.me/(?:c/)?([a-zA-Z0-9_-]+)/(\d+)', url)
    if match:
        chat_identifier_part = match.group(1)
        message_id = int(match.group(2))
        if url.find("/c/") != -1:
            try:
                chat_identifier = int('-100' + chat_identifier_part)
            except ValueError:
                return None, None
        else:
            chat_identifier = "@" + chat_identifier_part 
        return chat_identifier, message_id
    return None, None

# --------------------------------------------------------------------------------

## ENDPOINT 1: STREAMING (In-Browser Playback)
@app.route('/stream-telegram-video', methods=['GET'])
@check_api_key # <-- APPLY SECURITY CHECK
def stream_video():
    if not telegram_client or not telegram_client.is_connected:
        return {"error": "Telegram client is not connected. Check server logs."}, 503

    telegram_url = request.args.get('url')
    if not telegram_url: return {"error": "Missing 'url' parameter."}, 400
    chat_id, message_id = parse_link(telegram_url)
    if not chat_id or not message_id: return {"error": "Invalid Telegram URL format."}, 400

    try:
        message = telegram_client.get_messages(chat_id, message_ids=message_id)
        if isinstance(message, list): message = message[0] if message else None
        if not message or not message.video:
            return {"error": "Message does not contain a video or was not found."}, 404

        file_size = message.video.file_size
        file_name = message.video.file_name

        def generate_stream():
            try:
                for chunk in telegram_client.stream_media(message.video):
                    yield chunk
            except Exception as e:
                print(f"Streaming error during generation: {e}")

        response = Response(
            generate_stream(), 
            mimetype='video/mp4',
            content_type='video/mp4'
        )
        
        # CRITICAL FIX: 'inline' for streaming
        response.headers['Content-Disposition'] = f'inline; filename="{file_name}"' 
        response.headers['Content-Length'] = str(file_size)
        response.headers['Accept-Ranges'] = 'bytes'
        
        return response

    except UserNotParticipant:
        return {"error": "The authenticated account is not a member of this private chat or channel."}, 403
    except Exception as e:
        print(f"FATAL STREAMING SERVER ERROR: {e}")
        return {"error": f"An unexpected server error occurred: {e}"}, 500

## ENDPOINT 2: DOWNLOADING (Save to User System)
@app.route('/download-telegram-video', methods=['GET'])
@check_api_key # <-- APPLY SECURITY CHECK
def download_video():
    if not telegram_client or not telegram_client.is_connected:
        return {"error": "Telegram client is not connected. Check server logs."}, 503

    telegram_url = request.args.get('url')
    
    chat_id, message_id = parse_link(telegram_url)
    if not chat_id or not message_id: return {"error": "Invalid Telegram URL format..."}, 400

    try:
        message = telegram_client.get_messages(chat_id, message_ids=message_id)
        if not message or not message.video:
            return {"error": "Message does not contain a video or was not found."}, 404
        
        # 1. Download the full file to the server's disk (temp dir)
        temp_filename = f"{message.video.file_unique_id}_{message.video.file_name}"
        temp_file_path = os.path.join(tempfile.gettempdir(), temp_filename) 

        downloaded_file = telegram_client.download_media(
            message=message,
            file_name=temp_file_path
        )
        
        if not downloaded_file: return {"error": "Failed to download media from Telegram."}, 500

        # 2. Add the file path for cleanup later
        global DOWNLOADED_FILES_TO_CLEANUP
        DOWNLOADED_FILES_TO_CLEANUP.append(downloaded_file)

        # 3. Use Flask's send_file with 'as_attachment=True' (CRITICAL)
        response = send_file(
            downloaded_file,
            mimetype=message.video.mime_type,
            as_attachment=True, # Forces the browser to download
            download_name=message.video.file_name
        )
        
        return response

    except Exception as e:
        print(f"FATAL DOWNLOAD SERVER ERROR: {e}")
        return {"error": f"An unexpected server error occurred during download: {e}"}, 500

# --- Start the server ---
if __name__ == '__main__':
    app.run(debug=True, port=8000)