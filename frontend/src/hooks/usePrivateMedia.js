import { useEffect, useState } from 'react'
import axios from 'axios'

export default function usePrivateMedia(path) {
  const valid = /^\/api\/(?:uploads\/[a-f0-9]{32}\.[a-z0-9]{1,5}|aeos\/employees\/[a-zA-Z0-9_-]+\/identity\/media\/[a-f0-9]{64}\/[a-f0-9]{32}\.(?:png|jpe?g|webp))$/.test(path)
  const [resource, setResource] = useState(null)
  useEffect(() => {
    if (!valid) return
    const controller = new AbortController()
    let objectURL
    axios.get(path, { responseType: 'blob', signal: controller.signal }).then(({ data }) => {
      if (controller.signal.aborted) return
      objectURL = URL.createObjectURL(data)
      setResource({ path, url: objectURL })
    }).catch(() => {})
    return () => {
      controller.abort()
      if (objectURL) URL.revokeObjectURL(objectURL)
    }
  }, [path, valid])
  return resource?.path === path ? resource.url : null
}
