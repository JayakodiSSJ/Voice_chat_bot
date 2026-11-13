## Build Development Environment

### Back-end

- Make sure you have Python 

###### How is install Python ######

Windows/macOS/Linux:

Download from official site:

👉 https://www.python.org/downloads/

## Verify installation by opening terminal/command prompt and running:

python --version

###### ###################################################

- Clone this git repo

 ```bash
https://github.com/SLTDigitalLab/aisle-agent-store.git
```


### Front-end

- Make sure you have Node.js installed.

###### How is install Node ######

Windows/macOS/Linux:

Go to the official website and download the installer:

👉 https://nodejs.org/en/download/

# After installation, verify by opening a terminal/command prompt and running:

node -v
npm -v

###### ###################################################

- Navigate to frontend directory

```bash
cd chatbot
```

- Install Core Dependencies 

```bash
npm install react-scripts --save
npm install @react-three/fiber @react-three/drei
npm install react-router-dom
npm install @mui/material
npm install react-icons
npm install botframework-directlinejs
npm install botframework-webchat
npm install microsoft-cognitiveservices-speech-sdk
npm install react-dom
npm install @react-three/drei
```


## Start Development Environment

### Front-end

- Navigate to frontend directory

```bash
cd chatbot
```
- Start the app

```bash
npm start
```

### Back-end

- Navigate to cloned directory

```bash
cd Backend_Agentstore
```

- Run the Flask server

```bash
python app.py
```
