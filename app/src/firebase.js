// Punto unico di accesso ai dati. Il nome resta quello di Firebase per non toccare le 20 pagine dell'interfaccia:
// 'firebase/*' e' risolto da src/shim (vedi vite.config.js) e parla con l'API hlab del tuo server.
import { initializeApp } from 'firebase/app'
import { getAuth, GoogleAuthProvider } from 'firebase/auth'
import { getFirestore } from 'firebase/firestore'

const app = initializeApp({})
export const auth = getAuth(app)
export const db = getFirestore(app)
export const googleProvider = new GoogleAuthProvider()
