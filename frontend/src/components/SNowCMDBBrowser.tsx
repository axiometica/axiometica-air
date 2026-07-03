import { useState, useEffect, useCallback } from 'react'
import { getSnowCMDBSummary, getSnowCMDBList, searchSnowCMDB, getSnowCIRecord } from '../services/api'
import { SNowCIClass, SNowCI } from '../types'
import './SNowCMDBBrowser.css'
import { parseUTC } from '../utils/dateFormatter'

interface SNowCMDBBrowserProps {
  onClose: () => void
  darkMode?: boolean
}

// Fields to show as columns in the list view per class
const LIST_FIELDS: Record<string, string[]> = {
  cmdb_ci_service:          ['operational_status', 'business_criticality', 'environment', 'owned_by'],
  cmdb_ci_service_offering: ['operational_status', 'vendor', 'service_classification', 'parent'],
  cmdb_ci_server:           ['host_name', 'ip_address', 'os', 'environment', 'operational_status'],
  cmdb_ci_linux_server:     ['host_name', 'ip_address', 'os_version', 'environment', 'operational_status'],
  cmdb_ci_win_server:       ['host_name', 'ip_address', 'os', 'domain', 'operational_status'],
  cmdb_rel_ci:              ['parent', 'child', 'type'],
}

const FIELD_LABEL: Record<string, string> = {
  name: 'Name', operational_status: 'Status', business_criticality: 'Criticality',
  environment: 'Env', owned_by: 'Owner', vendor: 'Vendor', service_classification: 'Class',
  parent: 'Parent', host_name: 'Hostname', ip_address: 'IP', os: 'OS',
  os_version: 'OS Version', domain: 'Domain', type: 'Rel Type', child: 'Child',
}

export default function SNowCMDBBrowser({ onClose, darkMode: _darkMode }: SNowCMDBBrowserProps) {
  const [classes, setClasses]         = useState<SNowCIClass[]>([])
  const [activeClass, setActiveClass] = useState<string>('cmdb_ci_service')
  const [records, setRecords]         = useState<SNowCI[]>([])
  const [total, setTotal]             = useState(0)
  const [offset, setOffset]           = useState(0)
  const [loading, setLoading]         = useState(false)
  const [search, setSearch]           = useState('')
  const [searching, setSearching]     = useState(false)
  const [searchResults, setSearchResults] = useState<SNowCI[] | null>(null)
  const [detail, setDetail]           = useState<SNowCI | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const PAGE = 50

  // Load class summary on mount
  useEffect(() => {
    getSnowCMDBSummary().then(r => {
      setClasses(r.data.classes)
    }).catch(() => {})
  }, [])

  // Load records when class or page changes
  const loadRecords = useCallback(async () => {
    if (searchResults !== null) return   // search mode active
    setLoading(true)
    try {
      const res = await getSnowCMDBList(activeClass, PAGE, offset)
      setRecords(res.data.items)
      setTotal(res.data.total)
    } catch {
      setRecords([])
    } finally {
      setLoading(false)
    }
  }, [activeClass, offset, searchResults])

  useEffect(() => { loadRecords() }, [loadRecords])

  const handleSearch = async () => {
    if (!search.trim()) { setSearchResults(null); return }
    setSearching(true)
    try {
      const res = await searchSnowCMDB(search.trim(), activeClass, 100)
      setSearchResults(res.data)
    } catch {
      setSearchResults([])
    } finally {
      setSearching(false)
    }
  }

  const clearSearch = () => {
    setSearch('')
    setSearchResults(null)
  }

  const handleClassChange = (ci: string) => {
    setActiveClass(ci)
    setOffset(0)
    setSearchResults(null)
    setSearch('')
    setDetail(null)
  }

  const handleRowClick = async (sys_id: string) => {
    setDetailLoading(true)
    try {
      const res = await getSnowCIRecord(sys_id)
      setDetail(res.data)
    } catch {
      setDetail(null)
    } finally {
      setDetailLoading(false)
    }
  }

  const displayRecords = searchResults !== null ? searchResults : records
  const classFields    = LIST_FIELDS[activeClass] ?? ['operational_status', 'environment']

  const totalCached = classes.reduce((s, c) => s + c.count, 0)
  const neverSynced = !loading && classes.length > 0 && totalCached === 0

  return (
    <div className="sb-overlay" onClick={onClose}>
      <div className="sb-modal" onClick={e => e.stopPropagation()}>

        {/* Header */}
        <div className="sb-head">
          <div>
            <h3 className="sb-title">ServiceNow CMDB</h3>
            <p className="sb-subtitle">
              {neverSynced
                ? 'No data synced yet — run Sync Now from Connector Hub first'
                : `${totalCached.toLocaleString()} cached records across ${classes.filter(c => c.count > 0).length} classes`
              }
            </p>
          </div>
          <button className="sb-close" onClick={onClose}>✕</button>
        </div>

        {/* No-sync empty state OR browser body */}
        {neverSynced ? (
          <div className="sb-no-sync">
            <div className="sb-no-sync-icon">⟳</div>
            <h4 className="sb-no-sync-title">CMDB cache is empty</h4>
            <p className="sb-no-sync-body">
              The local cache has no ServiceNow data yet.<br />
              Close this dialog, then click <strong>Sync Now</strong> on the ServiceNow card to populate the cache.
            </p>
            <button className="sb-no-sync-btn" onClick={onClose}>Close &amp; Go Back</button>
          </div>
        ) : (
          <>
            <div className="sb-body">
              {/* Left: class tabs */}
              <div className="sb-sidebar">
                {classes.map(c => (
                  <button
                    key={c.ci_class}
                    className={`sb-class-btn ${activeClass === c.ci_class ? 'active' : ''}`}
                    onClick={() => handleClassChange(c.ci_class)}
                  >
                    <span className="sb-class-label">{c.label}</span>
                    <span className="sb-class-count">{c.count.toLocaleString()}</span>
                  </button>
                ))}
              </div>

              {/* Right: table */}
              <div className="sb-main">

                {/* Search bar */}
                <div className="sb-search-row">
                  <input
                    className="sb-search-input"
                    type="text"
                    placeholder={`Search ${classes.find(c => c.ci_class === activeClass)?.label ?? 'CIs'} by name…`}
                    value={search}
                    onChange={e => setSearch(e.target.value)}
                    onKeyDown={e => e.key === 'Enter' && handleSearch()}
                  />
                  <button className="sb-search-btn" onClick={handleSearch} disabled={searching}>
                    {searching ? '…' : 'Search'}
                  </button>
                  {searchResults !== null && (
                    <button className="sb-clear-btn" onClick={clearSearch}>Clear</button>
                  )}
                </div>

                {/* Record count */}
                <p className="sb-count">
                  {searchResults !== null
                    ? `${searchResults.length} search result${searchResults.length !== 1 ? 's' : ''}`
                    : `${total.toLocaleString()} records`
                  }
                </p>

                {/* Table */}
                <div className="sb-table-wrap">
                  <table className="sb-table">
                    <thead>
                      <tr>
                        <th>Name</th>
                        {classFields.map(f => <th key={f}>{FIELD_LABEL[f] ?? f}</th>)}
                        <th>Synced</th>
                      </tr>
                    </thead>
                    <tbody>
                      {loading ? (
                        <tr><td colSpan={classFields.length + 2} className="sb-empty">Loading…</td></tr>
                      ) : displayRecords.length === 0 ? (
                        <tr><td colSpan={classFields.length + 2} className="sb-empty">No records</td></tr>
                      ) : displayRecords.map(r => (
                        <tr
                          key={r.sys_id}
                          className={`sb-row ${detail?.sys_id === r.sys_id ? 'selected' : ''}`}
                          onClick={() => handleRowClick(r.sys_id)}
                        >
                          <td className="sb-name">{r.name || '—'}</td>
                          {classFields.map(f => (
                            <td key={f} className="sb-cell">{r[f] || '—'}</td>
                          ))}
                          <td className="sb-synced">
                            {r.synced_at ? parseUTC(r.synced_at).toLocaleDateString() : '—'}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>

                {/* Pagination (list mode only) */}
                {searchResults === null && total > PAGE && (
                  <div className="sb-pagination">
                    <button
                      className="sb-pg-btn"
                      disabled={offset === 0}
                      onClick={() => setOffset(Math.max(0, offset - PAGE))}
                    >Prev</button>
                    <span className="sb-pg-info">
                      {offset + 1}–{Math.min(offset + PAGE, total)} of {total}
                    </span>
                    <button
                      className="sb-pg-btn"
                      disabled={offset + PAGE >= total}
                      onClick={() => setOffset(offset + PAGE)}
                    >Next</button>
                  </div>
                )}
              </div>
            </div>

            {/* Detail panel */}
            {(detail || detailLoading) && (
              <div className="sb-detail">
                <div className="sb-detail-head">
                  <h4 className="sb-detail-title">{detail?.name ?? 'Loading…'}</h4>
                  <button className="sb-close-detail" onClick={() => setDetail(null)}>✕</button>
                </div>
                {detailLoading ? (
                  <p className="sb-empty">Loading record…</p>
                ) : detail && (
                  <div className="sb-detail-fields">
                    {Object.entries(detail)
                      .filter(([k]) => !['_ci_class'].includes(k) && k !== 'fields_json')
                      .map(([k, v]) => (
                        <div key={k} className="sb-detail-field">
                          <span className="sb-detail-key">{k.replace(/_/g, ' ')}</span>
                          <span className="sb-detail-val">{String(v || '—')}</span>
                        </div>
                      ))}
                  </div>
                )}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}
