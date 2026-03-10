import { useState, useEffect, useCallback, useRef } from 'react'
import { toast } from 'react-toastify'
import { api, type CalendarEvent, type EventCreateRequest, type EventUpdateRequest } from '@/api'
import { ConfirmModal } from '@/components/ConfirmModal'
import styles from './CalendarPage.module.css'

interface Props { active: boolean }

type CalView = 'list' | 'week'

const MONTHS = ['Jan','Fév','Mar','Avr','Mai','Juin','Juil','Aoû','Sep','Oct','Nov','Déc']
const DAY_NAMES = ['Lun','Mar','Mer','Jeu','Ven','Sam','Dim']
const START_H = 7
const END_H = 22
const PX_H = 44

function parseDate(e: CalendarEvent): Date | null {
  const s = e.datetime ?? e.date
  if (!s) return null
  return new Date(s)
}

function urgencyClass(d: Date): string {
  const now = new Date()
  const diff = (d.getTime() - now.getTime()) / 86400000
  if (diff < 1) return styles.today
  if (diff < 2) return styles.tomorrow
  if (diff < 7) return styles.soon
  return ''
}

function urgencyLabel(d: Date): string {
  const now = new Date()
  const diff = (d.getTime() - now.getTime()) / 86400000
  if (diff < 0) return ''
  if (diff < 1) return "Aujourd'hui"
  if (diff < 2) return 'Demain'
  // Dimanche de la semaine courante à 23:59:59 (semaine lun–dim)
  const daysUntilSunday = (7 - ((now.getDay() + 6) % 7)) % 7 || 7
  const sunday = new Date(now)
  sunday.setDate(now.getDate() + daysUntilSunday)
  sunday.setHours(23, 59, 59, 999)
  if (d <= sunday) return 'Cette semaine'
  return ''
}

function fmtTime(dt: string): string {
  return new Date(dt).toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' })
}

// Modal state
interface ModalState {
  uid: string
  title: string
  date: string
  time: string
  duration: number
  location: string
}

const EMPTY_MODAL: ModalState = { uid: '', title: '', date: '', time: '', duration: 60, location: '' }

type ConfirmState = { message: string; onConfirm: () => void } | null

export function CalendarPage({ active }: Props) {
  const [events, setEvents] = useState<CalendarEvent[]>([])
  const [writable, setWritable] = useState(false)
  const [view, setView] = useState<CalView>('list')
  const [weekOff, setWeekOff] = useState(0)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [modal, setModal] = useState<ModalState | null>(null)
  const [saving, setSaving] = useState(false)
  const [confirmState, setConfirmState] = useState<ConfirmState>(null)
  const confirmResolve = useRef<((v: boolean) => void) | null>(null)

  const askConfirm = (message: string): Promise<boolean> =>
    new Promise(resolve => {
      confirmResolve.current = resolve
      setConfirmState({ message, onConfirm: () => { setConfirmState(null); resolve(true) } })
    })

  const handleConfirmNo = () => {
    setConfirmState(null)
    confirmResolve.current?.(false)
  }

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const d = await api.getCalendar()
      setEvents(d.events)
      setWritable(d.writable)
      setError(null)
    } catch {
      setError('Calendrier non disponible')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (active) load()
  }, [active, load])

  // ── Modal helpers ──────────────────────────────────────────────
  const openCreate = () => setModal({ ...EMPTY_MODAL })

  const openEdit = (e: CalendarEvent) => {
    const d = parseDate(e)
    setModal({
      uid: e.uid,
      title: e.title,
      date: e.date ?? (d ? d.toISOString().slice(0, 10) : ''),
      time: e.datetime ? fmtTime(e.datetime) : '',
      duration: 60,
      location: e.location ?? '',
    })
  }

  const closeModal = () => setModal(null)

  const submit = async () => {
    if (!modal || !modal.title || !modal.date) return
    setSaving(true)
    try {
      if (modal.uid) {
        const body: EventUpdateRequest = {
          title: modal.title,
          date: modal.date,
          ...(modal.time && { time: modal.time }),
          duration_minutes: modal.duration,
          ...(modal.location && { location: modal.location }),
        }
        await api.updateEvent(modal.uid, body)
      } else {
        const body: EventCreateRequest = {
          title: modal.title,
          date: modal.date,
          ...(modal.time && { time: modal.time }),
          duration_minutes: modal.duration,
          ...(modal.location && { location: modal.location }),
        }
        await api.createEvent(body)
      }
      closeModal()
      await load()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Erreur')
    } finally {
      setSaving(false)
    }
  }

  const deleteEv = async (e: CalendarEvent) => {
    const ok = await askConfirm(`Supprimer "${e.title}" ?`)
    if (!ok) return
    try {
      await api.deleteEvent(e.uid)
      await load()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Erreur')
    }
  }

  // ── Week view helpers ──────────────────────────────────────────
  function getWeekStart(off: number): Date {
    const d = new Date()
    const day = (d.getDay() + 6) % 7 // Mon=0
    d.setDate(d.getDate() - day + off * 7)
    d.setHours(0, 0, 0, 0)
    return d
  }

  function weekDays(off: number): Date[] {
    const start = getWeekStart(off)
    return Array.from({ length: 7 }, (_, i) => {
      const d = new Date(start)
      d.setDate(d.getDate() + i)
      return d
    })
  }

  // ── Render ─────────────────────────────────────────────────────
  const days = weekDays(weekOff)
  const weekLabel = `${days[0].getDate()} ${MONTHS[days[0].getMonth()]} — ${days[6].getDate()} ${MONTHS[days[6].getMonth()]} ${days[6].getFullYear()}`

  return (
    <div className={styles.page}>
      {/* Header */}
      <div className={styles.header}>
        <div>
          <div className={styles.pageTitle}>Agenda</div>
          <div className={styles.pageSub}>
            {loading ? 'Chargement…' : error ? error : `${events.length} événements · 60 prochains jours`}
          </div>
        </div>
        <div className={styles.headerRight}>
          <div className={styles.viewToggle}>
            <button
              className={`${styles.viewBtn} ${view === 'list' ? styles.active : ''}`}
              onClick={() => setView('list')}
            >Liste</button>
            <button
              className={`${styles.viewBtn} ${view === 'week' ? styles.active : ''}`}
              onClick={() => setView('week')}
            >Semaine</button>
          </div>
          {writable && (
            <button className={styles.addBtn} onClick={openCreate}>+ Ajouter</button>
          )}
        </div>
      </div>

      {/* Week nav */}
      {view === 'week' && (
        <div className={styles.weekNav}>
          <button className={styles.weekNavBtn} onClick={() => setWeekOff(o => o - 1)}>←</button>
          <button className={styles.weekNavBtn} onClick={() => setWeekOff(0)}>Aujourd'hui</button>
          <span className={styles.weekNavLabel}>{weekLabel}</span>
          <button className={styles.weekNavBtn} onClick={() => setWeekOff(o => o + 1)}>→</button>
        </div>
      )}

      {/* Content */}
      <div className={styles.content}>
        {view === 'list' ? (
          <ListView events={events} writable={writable} onEdit={openEdit} onDelete={deleteEv} />
        ) : (
          <WeekView events={events} days={days} writable={writable} onEdit={openEdit} onDelete={deleteEv} />
        )}
      </div>

      {/* Modal */}
      {modal && (
        <div className={styles.modalOverlay} onClick={closeModal}>
          <div className={styles.modal} onClick={e => e.stopPropagation()}>
            <div className={styles.modalTitle}>{modal.uid ? 'Modifier' : 'Ajouter'} un événement</div>

            <div className={styles.formGroup}>
              <label className={styles.formLabel}>Titre *</label>
              <input
                className={styles.formInput}
                value={modal.title}
                onChange={e => setModal(m => m && ({ ...m, title: e.target.value }))}
                placeholder="Titre de l'événement"
                autoFocus
              />
            </div>
            <div className={styles.formRow}>
              <div className={styles.formGroup}>
                <label className={styles.formLabel}>Date *</label>
                <input
                  className={styles.formInput}
                  type="date"
                  value={modal.date}
                  onChange={e => setModal(m => m && ({ ...m, date: e.target.value }))}
                />
              </div>
              <div className={styles.formGroup}>
                <label className={styles.formLabel}>Heure</label>
                <input
                  className={styles.formInput}
                  type="time"
                  value={modal.time}
                  onChange={e => setModal(m => m && ({ ...m, time: e.target.value }))}
                />
              </div>
              <div className={styles.formGroup}>
                <label className={styles.formLabel}>Durée (min)</label>
                <input
                  className={styles.formInput}
                  type="number"
                  min={5}
                  step={5}
                  value={modal.duration}
                  onChange={e => setModal(m => m && ({ ...m, duration: Number(e.target.value) }))}
                />
              </div>
            </div>
            <div className={styles.formGroup}>
              <label className={styles.formLabel}>Lieu</label>
              <input
                className={styles.formInput}
                value={modal.location}
                onChange={e => setModal(m => m && ({ ...m, location: e.target.value }))}
                placeholder="Lieu (optionnel)"
              />
            </div>

            <div className={styles.modalActions}>
              <button className={styles.btnCancel} onClick={closeModal}>Annuler</button>
              <button className={styles.btnSave} onClick={submit} disabled={saving}>
                {saving ? '…' : 'Enregistrer'}
              </button>
            </div>
          </div>
        </div>
      )}

      {confirmState && (
        <ConfirmModal
          message={confirmState.message}
          confirmLabel="Supprimer"
          danger
          onConfirm={confirmState.onConfirm}
          onCancel={handleConfirmNo}
        />
      )}
    </div>
  )
}

// ── List View ──────────────────────────────────────────────────
interface ListProps {
  events: CalendarEvent[]
  writable: boolean
  onEdit: (e: CalendarEvent) => void
  onDelete: (e: CalendarEvent) => void
}

function ListView({ events, writable, onEdit, onDelete }: ListProps) {
  if (events.length === 0) return (
    <div className={styles.empty}>Aucun événement à venir</div>
  )

  return (
    <div className={styles.listEvents}>
      {events.map(e => {
        const d = parseDate(e)
        return (
          <div key={e.uid} className={`${styles.calEvent} ${d ? urgencyClass(d) : ''}`}>
            <div className={styles.dateBlock}>
              <span className={styles.dayNum}>{d?.getDate() ?? '—'}</span>
              <span className={styles.monthStr}>{d ? MONTHS[d.getMonth()] : ''}</span>
            </div>
            <div className={styles.calInfo}>
              <div className={styles.calTitle}>{e.title}</div>
              {e.datetime && <div className={styles.calTime}>{fmtTime(e.datetime)}</div>}
              {e.location && <div className={styles.calLocation}>📍 {e.location}</div>}
              {d && urgencyLabel(d) && (
                <span className={styles.calLabel}>{urgencyLabel(d)}</span>
              )}
            </div>
            {writable && (
              <div className={styles.calActions}>
                <button className={styles.calActionBtn} onClick={() => onEdit(e)}>✎</button>
                <button className={`${styles.calActionBtn} ${styles.delete}`} onClick={() => onDelete(e)}>✕</button>
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}

// ── Week View ──────────────────────────────────────────────────
interface WeekProps {
  events: CalendarEvent[]
  days: Date[]
  writable: boolean
  onEdit: (e: CalendarEvent) => void
  onDelete: (e: CalendarEvent) => void
}

function WeekView({ events, days, writable, onEdit, onDelete }: WeekProps) {
  const hours = Array.from({ length: END_H - START_H }, (_, i) => START_H + i)
  const totalH = (END_H - START_H) * PX_H

  function eventsForDay(day: Date): CalendarEvent[] {
    return events.filter(e => {
      const d = parseDate(e)
      if (!d) return false
      return d.toDateString() === day.toDateString()
    })
  }

  function evTop(e: CalendarEvent): number {
    const d = parseDate(e)
    if (!d) return 0
    return (d.getHours() - START_H) * PX_H + (d.getMinutes() * PX_H) / 60
  }

  function evHeight(e: CalendarEvent): number {
    const mins = e.duration_minutes ?? 60
    return Math.max((mins * PX_H) / 60, 20)
  }

  return (
    <div className={styles.weekOuter}>
      <div className={styles.weekInner}>
        {/* Hour gutter */}
        <div className={styles.weekGutter}>
          {hours.map(h => (
            <div key={h} className={styles.hourLabel} style={{ height: PX_H }}>
              {String(h).padStart(2, '0')}:00
            </div>
          ))}
        </div>

        {/* Day columns */}
        <div className={styles.weekDays}>
          {days.map((day, di) => {
            const now = new Date()
            const isToday = day.toDateString() === now.toDateString()
            const isPast = day < now && !isToday
            return (
              <div key={di} className={`${styles.weekDay} ${isPast ? styles.pastDay : ''}`}>
                <div className={`${styles.weekDayHdr} ${isToday ? styles.todayHdr : ''} ${isPast ? styles.pastHdr : ''}`}>
                  <span className={styles.dayName}>{DAY_NAMES[di]}</span>
                  <span className={styles.dayNum2}>{day.getDate()}</span>
                </div>
                <div className={styles.weekBody} style={{ height: totalH, position: 'relative' }}>
                  {/* Grid lines */}
                  {hours.map(h => (
                    <div key={h} className={styles.hLine} style={{ top: (h - START_H) * PX_H }} />
                  ))}
                  {/* Events */}
                  {eventsForDay(day).map(e => {
                    const top = evTop(e)
                    const height = evHeight(e)
                    return (
                      <div
                        key={e.uid}
                        className={`${styles.weekEv} ${urgencyClass(parseDate(e) ?? new Date())}`}
                        style={{ top, height }}
                      >
                        <span className={styles.weekEvTitle}>{e.title}</span>
                        {height > 30 && e.datetime && (
                          <span className={styles.weekEvTime}>{fmtTime(e.datetime)}</span>
                        )}
                        {writable && (
                          <div className={styles.weekEvActions}>
                            <button onClick={() => onEdit(e)}>✎</button>
                            <button onClick={() => onDelete(e)}>✕</button>
                          </div>
                        )}
                      </div>
                    )
                  })}
                </div>
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}