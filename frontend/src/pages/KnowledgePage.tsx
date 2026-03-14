import { useState, useEffect, useCallback, useRef } from 'react'
import { toast } from 'react-toastify'
import { api, type IngestedDocument } from '@/api'
import { ConfirmModal } from '@/components/ConfirmModal'
import styles from './KnowledgePage.module.css'

interface Props { active: boolean }

const ACCEPTED = '.pdf,.docx,.txt,.md,.py,.js,.ts,.c,.cpp,.h,.cs,.java,.sh,.bash,.ps1'
const ACCEPTED_LABEL = 'PDF · DOCX · TXT · MD · Python · JS · TS · C · C++ · Java · Shell'

export function KnowledgePage({ active }: Props) {
  const [docs, setDocs]         = useState<IngestedDocument[]>([])
  const [loaded, setLoaded]     = useState(false)
  const [ingesting, setIngesting] = useState(false)
  const [dragOver, setDragOver] = useState(false)
  const [ingestMsg, setIngestMsg] = useState<{ ok: boolean; text: string } | null>(null)
  const [confirmDoc, setConfirmDoc] = useState<IngestedDocument | null>(null)
  const confirmResolve = useRef<((v: boolean) => void) | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)

  const load = useCallback(async () => {
    try {
      const { documents } = await api.getDocuments()
      setDocs(documents)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Erreur de chargement')
    }
  }, [])

  useEffect(() => {
    if (active && !loaded) {
      setLoaded(true)
      load()
    }
  }, [active, loaded, load])

  const ingest = async (file: File) => {
    setIngesting(true)
    setIngestMsg(null)
    try {
      const result = await api.ingestFile(file)
      if (result.status === 'ingested') {
        setIngestMsg({ ok: true, text: `✓ ${result.filename} — ${result.chunks} chunks indexés` })
        await load()
      } else if (result.status === 'already_ingested') {
        setIngestMsg({ ok: false, text: `⚠ ${result.filename} déjà présent dans la base.` })
      } else {
        setIngestMsg({ ok: false, text: `${result.filename} : fichier vide, rien n'a été indexé.` })
      }
    } catch (e) {
      setIngestMsg({ ok: false, text: e instanceof Error ? e.message : 'Erreur d\'ingestion' })
    } finally {
      setIngesting(false)
    }
  }

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (file) ingest(file)
    e.target.value = ''   // reset pour re-sélectionner le même fichier si besoin
  }

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault()
    setDragOver(false)
    const file = e.dataTransfer.files[0]
    if (file) ingest(file)
  }

  const askDeleteConfirm = (doc: IngestedDocument): Promise<boolean> =>
    new Promise(resolve => {
      confirmResolve.current = resolve
      setConfirmDoc(doc)
    })

  const handleConfirmYes = () => { setConfirmDoc(null); confirmResolve.current?.(true) }
  const handleConfirmNo  = () => { setConfirmDoc(null); confirmResolve.current?.(false) }

  const handleDelete = async (doc: IngestedDocument) => {
    if (!doc.doc_id) return
    const ok = await askDeleteConfirm(doc)
    if (!ok) return
    try {
      await api.deleteDocument(doc.doc_id)
      setDocs(prev => prev.filter(d => d.doc_id !== doc.doc_id))
      toast.success(`"${doc.filename}" supprimé.`)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Erreur lors de la suppression')
    }
  }

  const fmtDate = (iso: string) => {
    try {
      return new Date(iso.replace(' ', 'T')).toLocaleDateString('fr-FR', {
        day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit',
      })
    } catch { return iso }
  }

  return (
    <div className={styles.page}>

      {/* ── Zone d'upload ─────────────────────────────────────── */}
      <section className={styles.uploadSection}>
        <h2 className={styles.sectionTitle}>Ingérer un fichier</h2>

        <div
          className={`${styles.dropZone} ${dragOver ? styles.dragOver : ''} ${ingesting ? styles.loading : ''}`}
          onDragOver={e => { e.preventDefault(); setDragOver(true) }}
          onDragLeave={() => setDragOver(false)}
          onDrop={handleDrop}
          onClick={() => !ingesting && fileInputRef.current?.click()}
        >
          <input
            ref={fileInputRef}
            type="file"
            accept={ACCEPTED}
            className={styles.hiddenInput}
            onChange={handleFileChange}
            disabled={ingesting}
          />
          {ingesting ? (
            <div className={styles.spinner} />
          ) : (
            <>
              <span className={styles.dropIcon}>📂</span>
              <span className={styles.dropLabel}>
                Glisse un fichier ici ou clique pour sélectionner
              </span>
            </>
          )}
          <span className={styles.dropHint}>{ACCEPTED_LABEL}</span>
        </div>

        {ingestMsg && (
          <div className={`${styles.ingestMsg} ${ingestMsg.ok ? styles.msgOk : styles.msgWarn}`}>
            {ingestMsg.text}
          </div>
        )}
      </section>

      {/* ── Liste des documents ───────────────────────────────── */}
      <section className={styles.docsSection}>
        <div className={styles.docsHeader}>
          <h2 className={styles.sectionTitle}>Connaissances indexées</h2>
          <span className={styles.docCount}>{docs.length} document{docs.length !== 1 ? 's' : ''}</span>
          <button className={styles.refreshBtn} onClick={load} title="Rafraîchir">↻</button>
        </div>

        {loaded && docs.length === 0 ? (
          <div className={styles.empty}>Aucun document ingéré.</div>
        ) : (
          <div className={styles.tableWrap}>
            <table className={styles.table}>
              <thead>
                <tr>
                  <th>Fichier</th>
                  <th className={styles.center}>Pages</th>
                  <th className={styles.center}>Chunks</th>
                  <th>Ingéré le</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {docs.map(doc => (
                  <tr key={doc.doc_id ?? doc.filename}>
                    <td className={styles.filename}>{doc.filename}</td>
                    <td className={styles.center}>{doc.pages}</td>
                    <td className={styles.center}>{doc.chunks}</td>
                    <td className={styles.date}>{fmtDate(doc.ingested_at)}</td>
                    <td className={styles.actions}>
                      {doc.doc_id && (
                        <button
                          className={styles.deleteBtn}
                          onClick={() => handleDelete(doc)}
                          title="Supprimer ce document"
                        >
                          🗑
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {confirmDoc && (
        <ConfirmModal
          message={`Supprimer "${confirmDoc.filename}" et ses ${confirmDoc.chunks} chunks de la base ? Cette action est irréversible.`}
          confirmLabel="Supprimer"
          danger
          onConfirm={handleConfirmYes}
          onCancel={handleConfirmNo}
        />
      )}
    </div>
  )
}