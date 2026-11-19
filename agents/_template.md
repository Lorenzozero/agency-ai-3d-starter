---
name: nome-agente            # minuscole e trattini, 2-31 caratteri
description: Quando usare questo agente, in una o due frasi (e' cio' che il COO legge per delegare).
model: sonnet                # fable | opus | sonnet | haiku | inherit (modello piu' forte solo dove serve)
effort: medium               # low | medium | high | xhigh | max
tools: []                    # strumenti nativi concessi: Read, Grep, Glob, WebFetch, WebSearch (mai Bash, Write, Edit)
mcp: []                      # gruppi di strumenti: crm_read
skills: []                   # cartelle in .claude/skills/
memory:
  private: true              # memoria vettoriale propria
  shared: read               # none | read | write (write solo per chi cura la memoria dell'azienda)
autonomy: 1                  # 0 osserva, 1 propone e aspetta l'ok, 2 esegue da solo azioni reversibili
max_turns: 12
---
Prompt di sistema dell'agente: ruolo, cosa puo' e non puo' fare, come lavora, formato della risposta.
I file che iniziano con _ vengono ignorati dal registro.
