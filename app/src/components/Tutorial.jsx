import React, { useState } from 'react'

const ADMIN_STEPS = [
  {
    icon: '👋',
    title: 'Benvenuto nel tuo ufficio di agenti!',
    desc: 'Questo è il centro di comando della tua squadra. Nei prossimi passi vedi tutto quello che puoi fare.',
  },
  {
    icon: '⊞',
    title: 'Il tuo pannello di controllo',
    desc: 'Qui vedi in tempo reale: quanti task ci sono per stato, chi sta lavorando adesso e i timer attivi di ognuno.',
  },
  {
    icon: '⏱️',
    title: 'Sistema di timer',
    desc: 'Ogni volta che qualcuno sposta un task in "In corso", il timer parte da solo. Come amministratore vedi il tempo esatto di ogni task attivo.',
  },
  {
    icon: '👥',
    title: 'Vista della squadra',
    desc: 'Vedi cosa fa ogni membro: task attivo, da fare e completati. Solo tu come amministratore vedi timer e statistiche.',
  },
  {
    icon: '☑',
    title: 'Elenco dei task',
    desc: 'Filtra per membro, stato, cliente o mese. Cambia lo stato direttamente dalla tabella. Esporta in CSV con un clic.',
  },
  {
    icon: '▦',
    title: 'Lavagna Kanban',
    desc: 'Trascina le schede tra le colonne per cambiare stato. Il timer parte o si ferma da solo in base allo spostamento.',
  },
  {
    icon: '◑',
    title: 'Metriche e utenti',
    desc: 'In Metriche vedi il rendimento della squadra: tempo per membro, task per cliente, ecc. In Utenti gestisci i ruoli.',
  },
  {
    icon: '🍅',
    title: 'Pomodoro — Sempre disponibile',
    desc: 'Il pulsante rosso 🍅 in basso a destra è il timer Pomodoro. 25 minuti di lavoro + 5 di pausa per rendere al meglio.',
  },
]

const EMPLOYEE_STEPS = [
  {
    icon: '👋',
    title: 'Benvenuto nel tuo ufficio di agenti!',
    desc: 'Qui gestisci il tuo lavoro e il tuo tempo. Nei prossimi passi ti spieghiamo come sfruttarlo al meglio.',
  },
  {
    icon: '⊞',
    title: 'Il tuo pannello personale',
    desc: 'Questa è la tua pagina iniziale. Mostra il tuo task attivo con il timer in tempo reale, i tuoi task da fare e le tue statistiche personali di produttività.',
  },
  {
    icon: '⏱️',
    title: 'Il timer parte da solo',
    desc: 'Quando sposti un task in "In corso" — dal modulo, dall\'elenco o dal Kanban — il timer parte da solo e ricevi una notifica.',
  },
  {
    icon: '👥',
    title: 'Vista della squadra',
    desc: 'Vedi su cosa stanno lavorando i colleghi: task attivo e da fare. Non vedi le loro statistiche private, solo ciò che hanno assegnato.',
  },
  {
    icon: '▦',
    title: 'Kanban: trascina i tuoi task',
    desc: 'Nella lavagna Kanban puoi trascinare i TUOI task tra le colonne. Se lo sposti in "In corso", il timer parte. Se lo sposti fuori, il tempo viene salvato.',
  },
  {
    icon: '🍅',
    title: 'Pomodoro per concentrarti',
    desc: 'Il pulsante 🍅 in basso a destra è il tuo timer Pomodoro. Lavora 25 minuti concentrato, riposa 5. Dopo 4 cicli arriva una pausa lunga!',
  },
  {
    icon: '👤',
    title: 'Il tuo profilo',
    desc: 'In "Il mio profilo" puoi aggiornare ruolo, telefono e bio. Vedi anche le tue statistiche: task completati, tempo medio per task.',
  },
]

export default function Tutorial({ isAdmin, onClose }) {
  const steps = isAdmin ? ADMIN_STEPS : EMPLOYEE_STEPS
  const [current, setCurrent] = useState(0)

  const step = steps[current]
  const isLast = current === steps.length - 1
  const isFirst = current === 0

  return (
    <div className="tutorial-overlay">
      <div className="tutorial-card">
        {/* Progress dots */}
        <div className="tutorial-step-indicator">
          {steps.map((_, i) => (
            <div
              key={i}
              className={`tutorial-dot ${i <= current ? 'done' : ''}`}
              style={{ cursor: 'pointer', flex: 1, height: 4, borderRadius: 99 }}
              onClick={() => setCurrent(i)}
            />
          ))}
        </div>

        {/* Content */}
        <div className="tutorial-icon">{step.icon}</div>
        <h2 className="tutorial-title">{step.title}</h2>
        <p className="tutorial-desc">{step.desc}</p>

        {/* Footer */}
        <div className="tutorial-footer">
          <span className="tutorial-progress">
            {current + 1} / {steps.length}
          </span>
          <div style={{ display: 'flex', gap: 8 }}>
            <button
              className="btn btn-secondary btn-sm"
              onClick={onClose}
              style={{ marginRight: 4 }}
            >
              Salta
            </button>
            {!isFirst && (
              <button
                className="btn btn-secondary btn-sm"
                onClick={() => setCurrent(c => c - 1)}
              >
                ← Indietro
              </button>
            )}
            {!isLast ? (
              <button
                className="btn btn-primary btn-sm"
                onClick={() => setCurrent(c => c + 1)}
              >
                Avanti →
              </button>
            ) : (
              <button
                className="btn btn-primary btn-sm"
                onClick={onClose}
                style={{ background: 'var(--success)' }}
              >
                Inizia! 🚀
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
