// HTTP API calls and the signed MQTT WebSocket URL.
import { fetchAuthSession } from 'aws-amplify/auth'
import { Sha256 } from '@aws-crypto/sha256-js'
import { SignatureV4 } from '@smithy/signature-v4'
import type { Config } from './config'

export async function api<T>(cfg: Config, path: string, method: 'GET' | 'POST' = 'GET', body?: object): Promise<T> {
  const { tokens } = await fetchAuthSession()
  const res = await fetch(cfg.apiUrl + path, {
    method,
    headers: { authorization: `Bearer ${tokens?.idToken?.toString()}`, ...(body ? { 'content-type': 'application/json' } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  })
  const data = await res.json()
  if (!res.ok) throw new Error(data?.message ?? res.statusText)
  return data as T
}

/** SigV4-presigned wss:// URL for IoT Core. The session token must be appended AFTER signing. */
export async function signedMqttUrl(cfg: Config): Promise<string> {
  const { credentials } = await fetchAuthSession({ forceRefresh: false })
  if (!credentials) throw new Error('no AWS credentials from the identity pool')
  const signer = new SignatureV4({
    service: 'iotdevicegateway',
    region: cfg.region,
    sha256: Sha256,
    credentials: { accessKeyId: credentials.accessKeyId, secretAccessKey: credentials.secretAccessKey },
  })
  const req = await signer.presign(
    { method: 'GET', protocol: 'wss:', hostname: cfg.iotEndpoint, path: '/mqtt', headers: { host: cfg.iotEndpoint } },
    { expiresIn: 900 },
  )
  const qs = new URLSearchParams(req.query as Record<string, string>)
  if (credentials.sessionToken) qs.set('X-Amz-Security-Token', credentials.sessionToken)
  return `wss://${cfg.iotEndpoint}/mqtt?${qs.toString()}`
}
