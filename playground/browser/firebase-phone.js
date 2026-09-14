import { initializeApp } from "firebase/app";
import {
  initializeAuth, inMemoryPersistence, RecaptchaVerifier, signInWithPhoneNumber, signOut,
} from "firebase/auth";

let auth;
let verifier;
let confirmation;
let confirmedUser;

export async function prepareVerification(config) {
  if (!auth) {
    const app = initializeApp(config, "ringtrunk-playground-phone");
    auth = initializeAuth(app, { persistence: inMemoryPersistence });
    auth.languageCode = "en";
  }
  confirmation = null;
  confirmedUser = null;
  verifier?.clear();
  verifier = new RecaptchaVerifier(auth, "phone-recaptcha", { size: "invisible" });
  return verifier.render();
}

export async function sendCode(config, phone) {
  await prepareVerification(config);
  try {
    confirmation = await signInWithPhoneNumber(auth, phone, verifier);
  } catch (error) {
    verifier.clear();
    verifier = null;
    throw error;
  }
}

export async function confirmCode(code) {
  // A backend timeout must not consume another OTP after Firebase accepted it.
  if (confirmedUser) return confirmedUser.getIdToken(true);
  if (!confirmation) throw { code: "auth/code-expired" };
  const result = await confirmation.confirm(code);
  confirmedUser = result.user;
  return confirmedUser.getIdToken(true);
}

export async function resetVerification() {
  confirmation = null;
  confirmedUser = null;
  verifier?.clear();
  verifier = null;
  if (auth) await signOut(auth);
}
