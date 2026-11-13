import { LogLevel } from "@azure/msal-browser";

export const msalConfig = {
  auth: {
    clientId: process.env.REACT_APP_MS_GRAPH_CLIENT_ID || "129b380d-814a-446d-8079-bc1f77526a36",
    authority: "https://login.microsoftonline.com/common",
    redirectUri: window.location.origin,
  },
  cache: {
    cacheLocation: "sessionStorage",
    storeAuthStateInCookie: false,
  },
  system: {
    loggerOptions: {
      loggerCallback: (level, message, containsPii) => {
        if (containsPii) {
          return;
        }
        switch (level) {
          case LogLevel.Error:
            console.error(message);
            return;
          case LogLevel.Info:
            console.info(message);
            return;
          case LogLevel.Verbose:
            console.debug(message);
            return;
          case LogLevel.Warning:
            console.warn(message);
            return;
          default:
            return;
        }
      }
    }
  }
};

export const loginRequest = {
  scopes: ["User.Read", "Files.Read", "Files.Read.All", "Sites.Read.All"]
};

export const tokenRequest = {
  scopes: ["Files.Read", "Files.Read.All", "Sites.Read.All"]
};
