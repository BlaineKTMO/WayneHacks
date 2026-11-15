import { Amplify } from 'aws-amplify'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import Demo from './Demo'
import { loadConfig } from './config'
import './index.css'

const root = createRoot(document.getElementById('root')!)

// Reload open tabs after a deploy: a page running old code can be rejected by the cloud
// (e.g. old MQTT topics), which looks like "connecting, dropping" forever.
async function deployedVersion(): Promise<string | null> {
  try { return (await (await fetch('/version.json', { cache: 'no-store' })).json()).version } catch { return null }
}
deployedVersion().then((mine) => {
  if (!mine) return
  setInterval(async () => {
    const now = await deployedVersion()
    if (now && now !== mine) window.location.reload()
  }, 30_000)
})

loadConfig()
  .then((cfg) => {
    Amplify.configure({
      Auth: {
        Cognito: {
          userPoolId: cfg.userPoolId,
          userPoolClientId: cfg.userPoolClientId,
          identityPoolId: cfg.identityPoolId,
          loginWith: { email: true },
        },
      },
    })
    const isDemo = window.location.pathname.replace(/\/$/, '') === '/demo'
    root.render(<StrictMode>{isDemo ? <Demo cfg={cfg} /> : <App cfg={cfg} />}</StrictMode>)
  })
  .catch((e) => root.render(<pre style={{ padding: 16 }}>{String(e)}</pre>))
