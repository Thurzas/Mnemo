import { useState } from 'react'
import type { OnboardingQuestion, OnboardingAnswerItem } from '@/api'
import { api } from '@/api'
import styles from './OnboardingModal.module.css'

interface Props {
  username: string
  questions: OnboardingQuestion[]
  onDone: () => void
}

export function OnboardingModal({ username, questions, onDone }: Props) {
  // step 0 = welcome, 1..n = questions, n+1 = done
  const [step, setStep]       = useState(0)
  const [answers, setAnswers] = useState<Record<string, string>>({})
  const [input, setInput]     = useState('')
  const [submitting, setSubmitting] = useState(false)

  const total   = questions.length
  const current = step >= 1 && step <= total ? questions[step - 1] : null

  const goNext = (save: boolean) => {
    if (save && current) {
      setAnswers(prev => ({ ...prev, [current.id]: input.trim() }))
    }
    setInput('')
    if (step < total) {
      setStep(step + 1)
    } else {
      submit(save && current ? { ...answers, [current.id]: input.trim() } : answers)
    }
  }

  const goPrev = () => {
    if (step <= 1) return
    const prev = questions[step - 2]
    setInput(answers[prev.id] ?? '')
    setStep(step - 1)
  }

  const submit = async (finalAnswers: Record<string, string>) => {
    setSubmitting(true)
    setStep(total + 1)
    try {
      const payload: OnboardingAnswerItem[] = questions
        .filter(q => finalAnswers[q.id]?.trim())
        .map(q => ({
          id:         q.id,
          answer:     finalAnswers[q.id],
          section:    q.section,
          subsection: q.subsection,
          label:      q.label,
        }))
      if (payload.length) await api.onboardingSubmit(payload)
    } catch {
      // silencieux — l'app reste utilisable
    } finally {
      setSubmitting(false)
      setTimeout(onDone, 1400)
    }
  }

  // ── Welcome ──────────────────────────────────────────────────────
  if (step === 0) {
    return (
      <div className={styles.overlay}>
        <div className={styles.card}>
          <div className={styles.icon}>🧠</div>
          <h2 className={styles.title}>Bienvenue, {username} !</h2>
          <p className={styles.subtitle}>
            Quelques questions rapides pour que Mnemo apprenne à te connaître.
            Tu pourras passer celles que tu veux.
          </p>
          <div className={styles.actions}>
            <button className={styles.btnSecondary} onClick={onDone}>Plus tard</button>
            <button className={styles.btnPrimary} onClick={() => setStep(1)}>Commencer →</button>
          </div>
        </div>
      </div>
    )
  }

  // ── Done ─────────────────────────────────────────────────────────
  if (step === total + 1) {
    return (
      <div className={styles.overlay}>
        <div className={styles.card}>
          <div className={styles.icon}>✓</div>
          <h2 className={styles.title}>C'est noté !</h2>
          <p className={styles.subtitle}>
            {submitting ? 'Enregistrement…' : 'Mnemo est prêt.'}
          </p>
        </div>
      </div>
    )
  }

  // ── Question ─────────────────────────────────────────────────────
  return (
    <div className={styles.overlay}>
      <div className={styles.card}>
        <div className={styles.progress}>
          <span className={styles.progressText}>Question {step} / {total}</span>
          <div className={styles.progressBar}>
            <div className={styles.progressFill} style={{ width: `${(step / total) * 100}%` }} />
          </div>
        </div>

        <p className={styles.question}>{current?.question}</p>

        <textarea
          className={styles.textarea}
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); goNext(true) } }}
          placeholder="Ta réponse… (ou laisse vide pour passer)"
          rows={2}
          autoFocus
        />

        <div className={styles.actions}>
          <button
            className={styles.btnGhost}
            onClick={goPrev}
            disabled={step <= 1}
          >
            ← Précédent
          </button>
          <button className={styles.btnSecondary} onClick={() => goNext(false)}>
            Passer
          </button>
          <button className={styles.btnPrimary} onClick={() => goNext(true)}>
            {step === total ? 'Terminer ✓' : 'Suivant →'}
          </button>
        </div>
      </div>
    </div>
  )
}