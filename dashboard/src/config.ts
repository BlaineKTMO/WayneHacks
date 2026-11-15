// Runtime config, written from the CDK stack outputs by `deploy.py` (or `deploy.py --local`).
export type Config = {
  region: string
  userPoolId: string
  userPoolClientId: string
  identityPoolId: string
  apiUrl: string
  iotEndpoint: string
  sites?: SiteInfo[]   // every warehouse this login can see (deploy.py)
  site?: string        // the one this view is showing; apiUrl is that site's API
}
export type SiteInfo = { id: string; name: string; apiUrl: string; simulated?: boolean }

export async function loadConfig(): Promise<Config> {
  const res = await fetch('/config.json', { cache: 'no-store' })
  if (!res.ok) throw new Error('config.json missing: run `uv run python dashboard/deploy.py --local`')
  return res.json()
}
