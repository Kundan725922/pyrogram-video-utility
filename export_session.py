import asyncio
from pyrogram import Client

# --- Configuration (Must match your existing session) ---
API_ID = 35172395 
API_HASH = "3cb710c4a835a23eeb73112026d46686"
SESSION_NAME = "streamer_session" # Must match your session file name

async def main():
    """Initializes the Pyrogram Client using the existing file and exports the session string."""
    print("--- Pyrogram Session String Exporter ---")
    
    try:
        # Initialize client using the EXISTING session file
        async with Client(SESSION_NAME, API_ID, API_HASH) as client:
            # Export the session as a string
            session_string = await client.export_session_string()
            
            print("\n✅ Session successfully exported.")
            print("=========================================================================")
            print("COPY THIS STRING (This is your PYROGRAM_SESSION value):")
            print(session_string) # <--- THIS IS THE VALUE YOU NEED!
            print("=========================================================================")
            
    except Exception as e:
        print(f"\n❌ Error exporting session. Ensure your '{SESSION_NAME}.session' file is in this folder.")
        print(f"Details: {e}")

if __name__ == "__main__":
    asyncio.run(main())