import { useEffect, useState } from 'react'
import axios from 'axios'
import authHeaders from '../../authHeaders'

/** مصفوفة الأدوار والصلاحيات (للعرض — الفرض الحقيقي في الخادم: app/auth/roles.py). */
const muted = 'text-text-secondary-light dark:text-text-secondary-dark'
const strong = 'text-text-primary-light dark:text-text-primary-dark'

const PermissionsAdmin = ({ notify }) => {
  const [data, setData] = useState(null)

  useEffect(() => {
    axios.get('/api/admin/permissions', { headers: authHeaders() })
      .then(res => setData(res.data))
      .catch(e => notify(e?.response?.data?.detail || 'تعذّر تحميل الصلاحيات', 'error'))
  }, [notify])

  if (!data) return null
  return (
    <div className="bg-card-light dark:bg-card-dark rounded-xl shadow-sm overflow-hidden">
      <div className="p-5 border-b border-border-light dark:border-border-dark">
        <h3 className={`text-lg font-semibold ${strong}`}>🛡️ الأدوار والصلاحيات</h3>
        <p className={`text-xs mt-1 ${muted}`}>كل دور يرث صلاحيات ما تحته. دورك: <b>{data.roles.find(r => r.id === data.me.role)?.label}</b></p>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 dark:bg-gray-800">
            <tr>
              <th className={`px-5 py-3 text-right text-xs ${muted}`}>الصلاحية</th>
              {data.roles.map(r => <th key={r.id} className={`px-5 py-3 text-center text-xs ${muted}`}>{r.label}</th>)}
            </tr>
          </thead>
          <tbody className="divide-y divide-border-light dark:divide-border-dark">
            {data.permissions.map(p => (
              <tr key={p.id}>
                <td className={`px-5 py-2 ${strong}`}>{p.label} <span className={`text-[10px] ${muted}`} dir="ltr">{p.id}</span></td>
                {data.roles.map(r => (
                  <td key={r.id} className="px-5 py-2 text-center">{p.roles.includes(r.id) ? '✅' : '—'}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

export default PermissionsAdmin
