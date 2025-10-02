#333333333333333333333333333333 OLD VERSION (NO HISTORY OR HANDLING ERRORS)
# import sys
# import os
# import pickle

# from google.auth.transport.requests import Request
# from google_auth_oauthlib.flow import InstalledAppFlow
# from googleapiclient.discovery import build

# # If modifying scopes, delete the token.pickle file.
# SCOPES = ['https://www.googleapis.com/auth/drive']


# def authenticate():
#     """
#     Authenticates the user using OAuth and returns a service object.
#     """
#     creds = None

#     # token.pickle stores the user's credentials after first run.
#     if os.path.exists('token.pickle'):
#         with open('token.pickle', 'rb') as token:
#             creds = pickle.load(token)

#     # If no valid credentials, log in.
#     if not creds or not creds.valid:
#         if creds and creds.expired and creds.refresh_token:
#             creds.refresh(Request())
#         else:
#             flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
#             creds = flow.run_local_server(port=0)
#         # Save the credentials for next time
#         with open('token.pickle', 'wb') as token:
#             pickle.dump(creds, token)

#     service = build('drive', 'v3', credentials=creds)
#     return service


# def copy_folder_contents(service, source_folder_id, destination_folder_id):
#     """
#     Recursively copies all files and subfolders from source_folder_id
#     to destination_folder_id.
#     """
#     page_token = None
#     while True:
#         # 1. List everything in the source folder
#         query = f"'{source_folder_id}' in parents and trashed=false"
#         response = (
#             service.files()
#             .list(q=query, fields="nextPageToken, files(id, name, mimeType)", pageToken=page_token)
#             .execute()
#         )
#         files = response.get('files', [])

#         for file_obj in files:
#             file_id = file_obj['id']
#             name = file_obj['name']
#             mime_type = file_obj['mimeType']

#             if mime_type == 'application/vnd.google-apps.folder':
#                 # Create a matching folder in the destination
#                 new_folder_id = create_folder(service, name, destination_folder_id)
#                 # Recursively copy items inside that folder
#                 copy_folder_contents(service, file_id, new_folder_id)
#             else:
#                 # Copy a file
#                 copy_file(service, file_id, name, destination_folder_id)
#                 print(f"Copied file: {name}")

#         page_token = response.get('nextPageToken')
#         if not page_token:
#             break


# def create_folder(service, folder_name, parent_id):
#     """
#     Creates a folder in Google Drive and returns its new folder ID.
#     """
#     file_metadata = {
#         'name': folder_name,
#         'mimeType': 'application/vnd.google-apps.folder',
#         'parents': [parent_id],
#     }

#     folder = service.files().create(body=file_metadata, fields='id').execute()
#     print(f"Created folder: {folder_name}")
#     return folder.get('id')


# def copy_file(service, source_file_id, new_title, parent_id):
#     """
#     Copies a file to the specified folder.
#     """
#     body = {
#         'name': new_title,
#         'parents': [parent_id],
#     }
#     return service.files().copy(fileId=source_file_id, body=body).execute()


# def main():
#     if len(sys.argv) != 3:
#         print("Usage: python main.py <SOURCE_FOLDER_ID> <DESTINATION_FOLDER_ID>")
#         sys.exit(1)

#     source_folder_id = sys.argv[1]
#     destination_folder_id = sys.argv[2]

#     service = authenticate()
#     copy_folder_contents(service, source_folder_id, destination_folder_id)
#     print("Copy operation completed!")


# if __name__ == '__main__':
#     main()


import os
import sys
import json
import time
import pickle
from datetime import datetime

# pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib
# ALSO ADDED REQUIRMENTS TO INSTALL USING `pip install -r requirements.txt`
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError


# 'drive' is full access, which is needed to read one account and write to another.
SCOPES = ['https://www.googleapis.com/auth/drive']
# Files to store credentials and progress
TOKEN_FILE = 'token.pickle'
PROGRESS_FILE = 'copy_progress.json'
FAILED_LOG_FILE = 'failed_files.log'


def authenticate():
    """Handles user authentication for the Google Drive API."""
    creds = None
    # The file TOKEN_FILE stores the user's access and refresh tokens.
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, 'rb') as token:
            creds = pickle.load(token)

    # If there are no (valid) credentials available, let the user log in.
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            # 'credentials.json' is downloaded from the Google Cloud Console.
            flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
            creds = flow.run_local_server(port=0)
        # Save the credentials for the next run
        with open(TOKEN_FILE, 'wb') as token:
            pickle.dump(creds, token)

    return build('drive', 'v3', credentials=creds)


def load_progress():
    """Loads the progress from a JSON file."""
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE, 'r') as f:
            return json.load(f)
    # If no progress file, start with a clean slate.
    # folder_map maps source folder IDs to their new destination IDs.
    # copied_files is a list of source file IDs that have been successfully copied.
    # THIS HANDLES IF ANY ERROR OCCURES YOU WON'T DUPLICATE FILES/FOLDERS
    return {'folder_map': {}, 'copied_files': []}


def save_progress(progress):
    """Saves the current progress to a JSON file."""
    with open(PROGRESS_FILE, 'w') as f:
        json.dump(progress, f, indent=4)


def log_failure(path, error):
    """Logs a failed file or folder operation to the log file."""
    with open(FAILED_LOG_FILE, 'a') as f:
        f.write(f"[{datetime.now().isoformat()}] Path: {path} | Error: {str(error)}\n")


def copy_folder_contents(service, source_folder_id, dest_folder_id, progress, path=""):
    """
    Recursively copies files and folders from a source to a destination,
    tracking progress and handling interruptions.
    """
    # Map the root source folder to the root destination folder to start
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
                pageSize=100  # Adjust page size as needed (max 1000)
            ).execute()
        except HttpError as e:
            print(f"❌ ERROR: Could not list files in folder ID '{source_folder_id}': {e}")
            log_failure(f"{path}/<folder_listing_failed>", e)
            return # Stop processing this folder if we can't list its contents

        items = response.get('files', [])
        
        for item in items:
            item_id = item['id']
            item_name = item['name']
            item_type = item['mimeType']
            current_path = os.path.join(path, item_name)

            if item_type == 'application/vnd.google-apps.folder':
                # --- FOLDER ---
                new_dest_folder_id = None
                if item_id in progress['folder_map']:
                    # This folder was already created in a previous run.
                    print(f"⏭️  SKIPPING Folder (already created): {current_path}")
                    new_dest_folder_id = progress['folder_map'][item_id]
                else:
                    # Create the new folder in the destination.
                    print(f"📁 Creating folder: {current_path}...")
                    folder_metadata = {
                        'name': item_name,
                        'mimeType': 'application/vnd.google-apps.folder',
                        'parents': [dest_folder_id]
                    }
                    try:
                        new_folder = service.files().create(body=folder_metadata, fields='id').execute()
                        new_dest_folder_id = new_folder['id']
                        # IMPORTANT: Save progress immediately after successful creation.
                        progress['folder_map'][item_id] = new_dest_folder_id
                        save_progress(progress)
                    except HttpError as e:
                        print(f"❌ ERROR creating folder '{current_path}': {e}")
                        log_failure(current_path, e)
                        continue # Skip to the next item
                
                # Recursively copy the contents of this subfolder.
                copy_folder_contents(service, item_id, new_dest_folder_id, progress, current_path)

            else:
                # --- FILE ---
                if item_id in progress['copied_files']:
                    # This file was already copied in a previous run.
                    print(f"⏭️  SKIPPING File (already copied): {current_path}")
                    continue

                print(f"📄 Copying file: {current_path}...")
                # The body only needs the new parent folder ID. Name and other metadata are copied.
                file_metadata = {'parents': [dest_folder_id]}
                
                retries = 3
                for i in range(retries):
                    try:
                        service.files().copy(fileId=item_id, body=file_metadata).execute()
                        # IMPORTANT: Save progress immediately after successful copy.
                        progress['copied_files'].append(item_id)
                        save_progress(progress)
                        print(f"✅ Copied: {current_path}")
                        break # Success, break the retry loop
                    except HttpError as e:
                        error_reason = json.loads(e.content).get('error', {}).get('errors', [{}])[0].get('reason', 'unknown')
                        if error_reason in ['userRateLimitExceeded', 'rateLimitExceeded']:
                            wait_time = (2 ** i) + 1 # Exponential backoff
                            print(f"⏳ RATE LIMIT HIT. Waiting for {wait_time}s and retrying... ({i+1}/{retries})")
                            time.sleep(wait_time)
                        elif error_reason == 'cannotCopyFile':
                            print(f"⚠️  SKIPPED (File not copyable): {current_path}")
                            log_failure(current_path, f"Permission error: {error_reason}")
                            break # Don't retry for permission errors
                        else:
                            print(f"❌ ERROR copying file '{current_path}': {e}")
                            log_failure(current_path, e)
                            break # Don't retry for other errors

        page_token = response.get('nextPageToken')
        if not page_token:
            break # Exit the loop when all pages are processed


def main():
    """Main function to orchestrate the copying process."""
    print("--- Google Drive Folder Forking Script ---")
    
    # Authenticate and get the service object
    try:
        service = authenticate()
        print("✓ Authentication successful.")
    except Exception as e:
        print(f"❌ Authentication failed: {e}")
        print("Please ensure your 'credentials.json' file is valid and in the same directory.")
        sys.exit(1)

    # Get folder IDs from user
    source_id = input("Enter the SOURCE folder ID: ").strip()
    dest_id = input("Enter the DESTINATION folder ID: ").strip()

    if not source_id or not dest_id:
        print("❌ Both source and destination folder IDs are required.")
        sys.exit(1)

    # Load existing progress
    progress = load_progress()

    print("\nStarting the copy process...")
    print(f"Source:      {source_id}")
    print(f"Destination: {dest_id}")
    print(f"Progress will be saved to '{PROGRESS_FILE}'")
    print(f"Errors will be logged to '{FAILED_LOG_FILE}'\n")

    try:
        copy_folder_contents(service, source_id, dest_id, progress)
        print("\n--- 🏁 Process Finished ---")
        copied_files_count = len(progress.get('copied_files', []))
        created_folders_count = len(progress.get('folder_map', {})) - 1 # Subtract the root
        print(f"Summary: Copied {copied_files_count} files and created {created_folders_count} folders.")
        if os.path.exists(FAILED_LOG_FILE):
             print(f"⚠️  Some items failed to copy. Check '{FAILED_LOG_FILE}' for details.")
        else:
            print("✨ All items copied successfully!")

    except KeyboardInterrupt:
        print("\n\n--- 🛑 Process Interrupted by User ---")
        print("Progress has been saved. You can safely rerun the script to resume.")
    except Exception as e:
        print(f"\n\n--- 💥 An Unexpected Error Occurred ---")
        print(f"Error: {e}")
        log_failure("FATAL_ERROR", e)
        print("Progress has been saved. You may be able to resume by rerunning the script.")


if __name__ == '__main__':
    main()