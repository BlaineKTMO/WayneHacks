import { Amplify } from 'aws-amplify'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
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
    root.render(<StrictMode><App cfg={cfg} /></StrictMode>)
  })
  .catch((e) => root.render(<pre style={{ padding: 16 }}>{String(e)}</pre>))
