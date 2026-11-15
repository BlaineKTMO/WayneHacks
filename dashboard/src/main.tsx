import { Amplify } from 'aws-amplify'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import Demo from './Demo'
import { loadConfig } from './config'
import './index.css'

const root = createRoot(document.getElementById('root')!)

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
