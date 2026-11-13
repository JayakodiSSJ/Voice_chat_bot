import { initializeApp } from "firebase/app";
import { getAuth, signInWithPopup, GoogleAuthProvider, signOut, onAuthStateChanged } from "firebase/auth";
import { getFirestore, doc, getDoc } from "firebase/firestore";

const firebaseConfig = {
  apiKey: "AIzaSyCi_ZK7pkb6eqxGdy6ElknRKpAuRcHWcRs",
  authDomain: "chatbot-7b4de.firebaseapp.com",
  projectId: "chatbot-7b4de",
  storageBucket: "chatbot-7b4de.firebasestorage.app",
  messagingSenderId: "543348663204",
  appId: "1:543348663204:web:6247c14aadc21027229731"
};

const app = initializeApp(firebaseConfig);
const auth = getAuth(app);
const db = getFirestore(app);
const provider = new GoogleAuthProvider();

// Google Sign-In - Simplified version
const signInWithGoogle = async () => {
  try {
    const result = await signInWithPopup(auth, provider);
    return result.user;
  } catch (error) {
    console.error("Error during sign-in:", error);
    throw error;
  }
};

// Logout function
const logout = async () => {
  try {
    await signOut(auth);
    window.location.href = "/login"; // Redirect to login after logout
  } catch (error) {
    console.error("Logout error:", error);
    throw error;
  }
};

// Get user role from Firestore
const getUserRole = async (userEmail) => {
  try {
    const userRef = doc(db, "users", userEmail);
    const userSnap = await getDoc(userRef);
    
    if (userSnap.exists()) {
      return userSnap.data().role;
    } else {
      return null;
    }
  } catch (error) {
    console.error("Error fetching user role:", error);
    return null;
  }
};

// Check if user has access to specific agent
const hasAgentAccess = (userRole, agentRole) => {
  // Admin and F role users have access to everything
  if (userRole === "admin" || userRole === "F") {
    return true;
  }
  // Users can only access their assigned agent
  return userRole === agentRole;
};

export { 
  auth, 
  db, 
  signInWithGoogle, 
  logout, 
  onAuthStateChanged, 
  getUserRole,
  hasAgentAccess
};