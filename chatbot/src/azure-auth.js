// azure-auth.js 
import { PublicClientApplication } from "@azure/msal-browser";

const msalConfig = {
  auth: {
    clientId: "129b380d-814a-446d-8079-bc1f77526a36",
    authority: "https://login.microsoftonline.com/534253fc-dfb6-462f-b5ca-cbe81939f5ee",
    redirectUri: window.location.origin
  },
  cache: {
    cacheLocation: "sessionStorage",
    storeAuthStateInCookie: false
  }
};

const loginRequest = {
  scopes: ["User.Read", "openid", "profile", "email"]
};

const msalInstance = new PublicClientApplication(msalConfig);

let isInitialized = false;
const initializeMsal = async () => {
  if (!isInitialized) {
    await msalInstance.initialize();
    isInitialized = true;
  }
  return msalInstance;
};

export const signInWithAzureAD = async () => {
  try {
    await initializeMsal();
    
    const loginRequest = {
      scopes: ["openid", "profile", "User.Read"],
      prompt: "select_account"
    };

    const response = await msalInstance.loginPopup(loginRequest);
    console.log("Login successful:", response);
    
    // Store the account info
    if (response.account) {
      sessionStorage.setItem('currentAccount', JSON.stringify(response.account));
    }
    
    return response.account;
  } catch (error) {
    console.error("Azure AD sign-in error:", error);
    
    // Don't treat user cancellation as an error
    if (error.message?.includes('user_cancelled')) {
      console.log("User cancelled the login flow");
      return null;
    }
    
    throw error;
  }
};

export const getCurrentAccount = () => {
  try {
    // First try to get from MSAL instance
    if (isInitialized) {
      const accounts = msalInstance.getAllAccounts();
      if (accounts.length > 0) {
        return accounts[0];
      }
    }
    
    // Fallback to sessionStorage
    const storedAccount = sessionStorage.getItem('currentAccount');
    return storedAccount ? JSON.parse(storedAccount) : null;
  } catch (error) {
    console.error("Error getting current account:", error);
    return null;
  }
};

export const signOut = async () => {
  try {
    await initializeMsal();
    
    // Clear session storage
    sessionStorage.removeItem('currentAccount');
    
    // Sign out from MSAL
    await msalInstance.logoutPopup();
  } catch (error) {
    console.error("Sign out error:", error);
    // Even if logout fails, clear local storage
    sessionStorage.removeItem('currentAccount');
  }
};

// Fixed getUserRole function with proper error handling
export const getUserRole = async () => {
  try {
    const account = getCurrentAccount();
    if (!account) {
      console.warn('No authenticated user found');
      return 'user';
    }

    console.log('Fetching role for email:', account.username);
    const apiResponse = await fetch('http://localhost:5000/api/user-role', {  // Add full URL
      method: 'GET',
      headers: {
        'Content-Type': 'application/json',
        'X-User-Email': account.username
      }
    });

    if (!apiResponse.ok) {
      console.error(`Role fetch failed with status ${apiResponse.status}: ${apiResponse.statusText}`);
      return 'user';
    }

    const data = await apiResponse.json();
    const role = data.role || 'user';
    console.log('User role from API:', role);
    return role;
  } catch (error) {
    console.error('Error fetching user role:', error);
    return 'user';
  }
};


// Fixed onAuthStateChanged to prevent infinite loops
let authCheckInterval = null;
let lastAuthState = null;

export const onAuthStateChanged = (callback) => {
  const checkAuthState = () => {
    const currentAccount = getCurrentAccount();
    const currentState = currentAccount ? currentAccount.username : null;
    
    // Only call callback if state actually changed
    if (currentState !== lastAuthState) {
      lastAuthState = currentState;
      callback(currentAccount);
    }
  };


  // Check immediately
  checkAuthState();

  return () => {};
};
