import { Message } from "./types";

const DB_NAME = "immigration-assistant";
const DB_VERSION = 1;
const STORE_MESSAGES = "messages";
const STORE_META = "meta";
const META_KEY = "summary";

export interface Meta {
  summary: string;
  summarizedUpTo: number;
}

function openDatabase(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, DB_VERSION);
    request.onupgradeneeded = () => {
      const db = request.result;
      if (!db.objectStoreNames.contains(STORE_MESSAGES)) {
        db.createObjectStore(STORE_MESSAGES, { keyPath: "id" });
      }
      if (!db.objectStoreNames.contains(STORE_META)) {
        db.createObjectStore(STORE_META, { keyPath: "key" });
      }
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

export async function getMessages(): Promise<Message[]> {
  const db = await openDatabase();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_MESSAGES, "readonly");
    const request = tx.objectStore(STORE_MESSAGES).getAll();
    request.onsuccess = () => {
      const messages = (request.result as Message[]).sort((a, b) => a.createdAt - b.createdAt);
      resolve(messages);
    };
    request.onerror = () => reject(request.error);
  });
}

export async function addMessage(message: Message): Promise<void> {
  const db = await openDatabase();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_MESSAGES, "readwrite");
    tx.objectStore(STORE_MESSAGES).put(message);
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
  });
}

export async function getMeta(): Promise<Meta | undefined> {
  const db = await openDatabase();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_META, "readonly");
    const request = tx.objectStore(STORE_META).get(META_KEY);
    request.onsuccess = () => resolve(request.result as Meta | undefined);
    request.onerror = () => reject(request.error);
  });
}

export async function setMeta(meta: Meta): Promise<void> {
  const db = await openDatabase();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_META, "readwrite");
    tx.objectStore(STORE_META).put({ key: META_KEY, ...meta });
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
  });
}

export async function clearAll(): Promise<void> {
  const db = await openDatabase();
  return new Promise((resolve, reject) => {
    const tx = db.transaction([STORE_MESSAGES, STORE_META], "readwrite");
    tx.objectStore(STORE_MESSAGES).clear();
    tx.objectStore(STORE_META).clear();
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
  });
}
