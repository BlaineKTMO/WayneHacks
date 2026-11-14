// Runtime config, written from the CDK stack outputs by `deploy.py` (or `deploy.py --local`).
export type Config = {
  region: string
  userPoolId: string
  userPoolClientId: string
  identityPoolId: string
  apiUrl: string
  iotEndpoint: string
}

export async function loadConfig(): Promise<Config> {
  const res = await fetch('/config.json', { cache: 'no-store' })
  if (!res.ok) throw new Error('config.json missing: run `uv run python dashboard/deploy.py --local`')
  return res.json()
}
