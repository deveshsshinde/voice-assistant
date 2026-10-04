# Voice Assistant

A simple desktop voice assistant written in Python. I built this to get familiar with audio processing, API integration, and standard GUI libraries. It listens for basic voice commands and executes system tasks.

## What it does
* Processes voice input using `SpeechRecognition`
* Provides audio feedback via `pyttsx3`
* Opens standard applications and performs basic web searches based on verbal prompts
* Includes a basic Tkinter UI to show when the mic is actively listening

## Tech Stack
* Python 3.11
* Tkinter
* SpeechRecognition
* pyttsx3

## Running it locally

1. Clone the repo:
   ```bash
   git clone https://github.com/deveshsshinde/voice-assistant.git
   ```
2. Install the required packages:
   ```bash
   pip install -r requirements.txt
   ```
3. Run the script:
   ```bash
   python voice_assistant.py
   ```
