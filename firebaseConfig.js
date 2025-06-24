// static/firebaseConfig.js
import { initializeApp } from "https://www.gstatic.com/firebasejs/10.12.2/firebase-app.js";
import { getAnalytics } from "https://www.gstatic.com/firebasejs/10.12.2/firebase-analytics.js";

const firebaseConfig = {
  apiKey: "AIzaSyCxjQ30GOJs0tmHCMOI4_5GVYGQ56IjCLk",
  authDomain: "heritage-web-1825e.firebaseapp.com",
  projectId: "heritage-web-1825e",
  storageBucket: "heritage-web-1825e.appspot.com",
  messagingSenderId: "757973125122",
  appId: "1:757973125122:web:5fd7efa1ae9dc8bf7d7f1b",
  measurementId: "G-JMD9XJM6YB"
};

const app = initializeApp(firebaseConfig);
const analytics = getAnalytics(app);
