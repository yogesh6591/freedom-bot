'use client'

/** FB-048 resale price rules + FB-049 live consult calculator. */

import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import {
  AlertTriangle,
  Check,
  CheckCircle2,
  Copy,
  History,
  Lock,
  Minus,
  Plus,
  Save,
  ShieldCheck,
  Sparkles
} from 'lucide-react'
import { api } from '@/lib/api'
import { cn } from '@/lib/utils'
import {
  Badge,
  Btn,
  ErrorNote,
  Field,
  LoadingBlock,
  Panel,
  SegmentedTab,
  SegmentedTabs,
  inputClass,
  timestamp
} from '@/components/common/primitives'

type QuoteStatus = 'draft' | 'pending_venu_confirm' | 'venu_confirmed' | 'not_configured'

interface ScopeRules {
  addon_usd: Record<string, number>
  autonomy_factor: Record<string, number>
  integrations_included: number
  integration_usd: number
  round_to_usd: number
  min_margin_percent: number
  max_discount_percent: number
  customized?: boolean
}

interface Rules {
  configured: boolean
  placeholder: boolean
  disclaimer?: string
  customer_price_usd: number | null
  cepoch_cost_usd: number | null
  margin_percent: number | null
  quote_status: QuoteStatus
  notes: string
  price_set_by?: string
  confirm_by?: string
  confirmed_at?: string | null
  confirmed_by?: string | null
  reconfirm_needed?: boolean
  forbidden_legacy_examples?: string[]
  scope_rules: ScopeRules
  rules_history?: {
    at: string
    by: string
    customer_price_usd: number
    margin_percent: number
    quote_status: QuoteStatus
    event: string
  }[]
}

interface ScopeConfig {
  base_package_domains: string[]
  addon_domains: string[]
  addon_usd: Record<string, number>
  autonomy_factor: Record<string, number>
  integrations_included: number
  integration_usd: number
  max_discount_percent: number
  min_margin_percent: number
}

interface QuoteLine {
  kind: string
  domain: string
  label: string
  detail?: string
  customer_usd: number
}

interface Quote {
  placeholder: boolean
  customer_price_usd: number
  cepoch_cost_usd: number
  gross_profit_usd: number
  margin_percent: number
  min_margin_percent: number
  below_floor: boolean
  subtotal_usd: number
  lines: QuoteLine[]
  warnings: string[]
  talk_track: string
  inputs: {
    addons: string[]
    autonomy: string
    integrations: number
    discount_percent: number
  }
}

interface SavedQuote extends Quote {
  id: string
  label: string
  saved_at: string
  saved_by: string
}

const AUTONOMY: { key: string; label: string; hint: string }[] = [
  { key: 'ADVISE', label: 'Advise', hint: 'Answers only' },
  { key: 'DRAFT', label: 'Draft', hint: 'Prepares, people send' },
  { key: 'WAIT_FOR_APPROVAL', label: 'Approve', hint: 'Acts after approval' },
  { key: 'AUTO_WITHIN_SCOPE', label: 'Auto', hint: 'Acts within scope' }
]

const STEPS: { key: QuoteStatus; label: string }[] = [
  { key: 'draft', label: 'Draft' },
  { key: 'pending_venu_confirm', label: 'Waiting on Venu' },
  { key: 'venu_confirmed', label: 'Confirmed by Venu' }
]

function usd(value?: number | null, cents = false) {
  if (value == null || Number.isNaN(value)) return '—'
  return value.toLocaleString('en-US', {
    style: 'currency',
    currency: 'USD',
    maximumFractionDigits: cents ? 2 : 0,
    minimumFractionDigits: 0
  })
}

function numOrNull(value: string): number | null {
  if (value.trim() === '') return null
  const n = Number(value)
  return Number.isFinite(n) ? n : null
}

export function PricingStudio() {
  const [rules, setRules] = useState<Rules | null>(null)
  const [config, setConfig] = useState<ScopeConfig | null>(null)
  const [saved, setSaved] = useState<SavedQuote[]>([])
  const [error, setError] = useState<string | null>(null)
  const [loaded, setLoaded] = useState<SavedQuote | null>(null)

  const load = useCallback(async () => {
    try {
      const [r, c, q] = await Promise.all([
        api.get<{ rules: Rules }>('/api/pricing/resale-rules'),
        api.get<{ config: ScopeConfig }>('/api/pricing/consult-config'),
        api.get<{ items: SavedQuote[] }>('/api/pricing/consult-quotes')
      ])
      setRules(r.rules)
      setConfig(c.config)
      setSaved(q.items)
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load pricing')
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  if (error && !rules) return <ErrorNote>{error}</ErrorNote>
  if (!rules || !config) return <LoadingBlock rows={4} />

  return (
    <div className="space-y-6">
      <StatusHeader rules={rules} onChanged={load} />
      <div className="grid gap-6 xl:grid-cols-2">
        <PriceRules rules={rules} onSaved={load} />
        <ScopeWeights rules={rules} config={config} onSaved={load} />
      </div>
      <ConsultCalculator
        rules={rules}
        config={config}
        loaded={loaded}
        onSaved={load}
      />
      <SavedQuotes items={saved} onLoad={setLoaded} />
    </div>
  )
}

/* ------------------------------------------------------------------ status */

function StatusHeader({ rules, onChanged }: { rules: Rules; onChanged: () => Promise<void> }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const current = STEPS.findIndex((s) => s.key === rules.quote_status)

  async function setStatus(status: QuoteStatus) {
    setBusy(true)
    setError(null)
    try {
      await api.put('/api/pricing/resale-rules', { quote_status: status })
      await onChanged()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not update status')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="overflow-hidden rounded-xl border border-border bg-background-secondary shadow-sm">
      <div className="bg-gradient-to-r from-brand/15 via-brand/5 to-transparent px-5 py-5">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <p className="type-caption text-brand">Custom resale pricing</p>
            <h2 className="mt-1 font-geist text-xl font-semibold text-primary">
              {rules.configured ? usd(rules.customer_price_usd) : 'No price set yet'}
              {rules.configured && (
                <span className="ml-3 text-sm font-medium text-muted">
                  base package · {rules.margin_percent}% margin
                </span>
              )}
            </h2>
            <p className="mt-1 text-xs text-muted">
              {rules.price_set_by || 'JeanneCAIO'} sets price and margin ·{' '}
              {rules.confirm_by || 'Venu'} confirms commercials
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <Guard>No public package price</Guard>
            <Guard>No per-seat pricing</Guard>
            <Guard>
              {(rules.forbidden_legacy_examples || ['$2K', '$75K']).slice(0, 2).join(' / ')} blocked
            </Guard>
          </div>
        </div>

        {rules.configured && (
          <div className="mt-5 flex flex-wrap items-center gap-3">
            <ol className="flex flex-1 items-center gap-2">
              {STEPS.map((step, i) => {
                const done = i < current || (i === current && step.key === 'venu_confirmed')
                const active = i === current
                return (
                  <li key={step.key} className="flex flex-1 items-center gap-2">
                    <span
                      className={cn(
                        'flex h-7 w-7 shrink-0 items-center justify-center rounded-full border text-xs font-semibold ui-transition',
                        done
                          ? 'border-positive bg-positive text-white'
                          : active
                            ? 'border-brand bg-brand text-white'
                            : 'border-border bg-background-elevated text-muted'
                      )}
                    >
                      {done ? <Check className="h-3.5 w-3.5" /> : i + 1}
                    </span>
                    <span
                      className={cn(
                        'text-xs font-medium',
                        active ? 'text-primary' : 'text-muted'
                      )}
                    >
                      {step.label}
                    </span>
                    {i < STEPS.length - 1 && (
                      <span
                        className={cn(
                          'h-px flex-1',
                          i < current ? 'bg-positive' : 'bg-border'
                        )}
                      />
                    )}
                  </li>
                )
              })}
            </ol>
            {rules.quote_status === 'draft' && (
              <Btn size="sm" variant="secondary" loading={busy} onClick={() => void setStatus('pending_venu_confirm')}>
                Send to Venu
              </Btn>
            )}
            {rules.quote_status === 'pending_venu_confirm' && (
              <Btn size="sm" variant="positive" loading={busy} onClick={() => void setStatus('venu_confirmed')}>
                <ShieldCheck className="h-4 w-4" /> Venu confirmed
              </Btn>
            )}
          </div>
        )}
        {rules.quote_status === 'venu_confirmed' && rules.confirmed_at && (
          <p className="mt-3 flex items-center gap-1.5 text-xs text-positive">
            <CheckCircle2 className="h-3.5 w-3.5" /> Confirmed {timestamp(rules.confirmed_at)}
          </p>
        )}
        {rules.reconfirm_needed && (
          <p className="mt-3 flex items-center gap-1.5 rounded-lg border border-warning/30 bg-warning/10 px-3 py-2 text-xs text-warning">
            <AlertTriangle className="h-3.5 w-3.5" /> Numbers changed after Venu confirmed — they need
            his confirmation again.
          </p>
        )}
        {error && <div className="mt-3"><ErrorNote>{error}</ErrorNote></div>}
      </div>
    </div>
  )
}

function Guard({ children }: { children: ReactNode }) {
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full border border-border bg-background-secondary px-3 py-1 text-[11px] font-medium text-secondary">
      <Lock className="h-3 w-3" />
      {children}
    </span>
  )
}

/* ------------------------------------------------------------------ rules */

function PriceRules({ rules, onSaved }: { rules: Rules; onSaved: () => Promise<void> }) {
  const [price, setPrice] = useState('')
  const [mode, setMode] = useState<'margin' | 'cost'>('margin')
  const [cost, setCost] = useState('')
  const [margin, setMargin] = useState('')
  const [notes, setNotes] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [flash, setFlash] = useState<string | null>(null)
  const [showHistory, setShowHistory] = useState(false)

  useEffect(() => {
    setPrice(rules.customer_price_usd != null ? String(rules.customer_price_usd) : '')
    setCost(rules.cepoch_cost_usd != null ? String(rules.cepoch_cost_usd) : '')
    setMargin(rules.margin_percent != null ? String(rules.margin_percent) : '')
    setNotes(rules.notes || '')
  }, [rules])

  const p = numOrNull(price)
  const derived = useMemo(() => {
    if (p == null || p <= 0) return null
    if (mode === 'margin') {
      const m = numOrNull(margin)
      if (m == null) return null
      return { cost: p * (1 - m / 100), margin: m }
    }
    const c = numOrNull(cost)
    if (c == null) return null
    return { cost: c, margin: ((p - c) / p) * 100 }
  }, [p, mode, margin, cost])

  const legacy = p === 2000 || p === 75000
  const negative = derived != null && derived.margin < 0

  async function save() {
    setBusy(true)
    setError(null)
    setFlash(null)
    try {
      const body: Record<string, unknown> = { customer_price_usd: p, notes }
      if (mode === 'margin') body.margin_percent = numOrNull(margin)
      else body.cepoch_cost_usd = numOrNull(cost)
      if (!rules.configured) body.quote_status = 'draft'
      const res = await api.put<{ rules: Rules }>('/api/pricing/resale-rules', body)
      setFlash(
        res.rules.reconfirm_needed
          ? 'Saved — Venu needs to confirm the new numbers.'
          : `Saved at ${res.rules.margin_percent}% margin.`
      )
      await onSaved()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Save failed')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Panel
      title="Price & margin"
      description="Your base package price for this client and what Cepoch keeps."
    >
      {error && <div className="mb-3"><ErrorNote>{error}</ErrorNote></div>}
      <div className="space-y-4">
        <Field label="Customer price (base package)">
          <MoneyInput value={price} onChange={setPrice} placeholder="e.g. 18000" />
        </Field>
        {legacy && (
          <p className="flex items-center gap-1.5 text-xs text-error">
            <AlertTriangle className="h-3.5 w-3.5" /> That is an old package price and will be refused.
          </p>
        )}

        <div>
          <SegmentedTabs aria-label="Set cost or margin">
            <SegmentedTab active={mode === 'margin'} onClick={() => setMode('margin')}>
              Set target margin
            </SegmentedTab>
            <SegmentedTab active={mode === 'cost'} onClick={() => setMode('cost')}>
              Set Cepoch cost
            </SegmentedTab>
          </SegmentedTabs>
          <div className="mt-3">
            {mode === 'margin' ? (
              <div className="flex items-center gap-3">
                <input
                  type="range"
                  min={0}
                  max={80}
                  step={0.5}
                  value={numOrNull(margin) ?? 0}
                  onChange={(e) => setMargin(e.target.value)}
                  className="flex-1 accent-[rgb(var(--color-brand))]"
                  aria-label="Target margin"
                />
                <div className="relative w-24">
                  <input
                    className={cn(inputClass, 'pr-7 text-right')}
                    type="number"
                    value={margin}
                    onChange={(e) => setMargin(e.target.value)}
                  />
                  <span className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-sm text-muted">%</span>
                </div>
              </div>
            ) : (
              <MoneyInput value={cost} onChange={setCost} placeholder="Cepoch delivery cost" />
            )}
          </div>
        </div>

        <MarginBar price={p} cost={derived?.cost ?? null} />
        {negative && (
          <p className="text-xs text-error">Cost is above price — that is a negative margin.</p>
        )}

        <Field label="Notes (internal)">
          <textarea
            className={`${inputClass} min-h-[64px]`}
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            placeholder="Context for Venu, assumptions, what was agreed"
          />
        </Field>

        <div className="flex flex-wrap items-center gap-3">
          <Btn
            size="sm"
            loading={busy}
            disabled={p == null || p <= 0 || derived == null || legacy || negative}
            onClick={() => void save()}
          >
            <Save className="h-4 w-4" /> Save price rules
          </Btn>
          {flash && <span className="text-xs text-positive">{flash}</span>}
          {(rules.rules_history?.length ?? 0) > 0 && (
            <Btn variant="ghost" size="sm" className="ml-auto" onClick={() => setShowHistory((v) => !v)}>
              <History className="h-4 w-4" /> {showHistory ? 'Hide' : 'Show'} changes
            </Btn>
          )}
        </div>

        <AnimatePresence>
          {showHistory && (
            <motion.ul
              initial={{ opacity: 0, height: 0 }}
              animate={{ opacity: 1, height: 'auto' }}
              exit={{ opacity: 0, height: 0 }}
              className="space-y-1.5 overflow-hidden border-t border-border pt-3 text-xs text-muted"
            >
              {[...(rules.rules_history || [])].reverse().map((h, i) => (
                <li key={`${h.at}-${i}`} className="flex flex-wrap items-center gap-2">
                  <span className="opacity-70">{timestamp(h.at)}</span>
                  <Badge
                    tone={h.event === 'confirmed' ? 'good' : h.event === 'reconfirm_needed' ? 'warn' : 'neutral'}
                  >
                    {h.event === 'confirmed' ? 'Confirmed' : h.event === 'reconfirm_needed' ? 'Needs Venu again' : 'Updated'}
                  </Badge>
                  <span>
                    {usd(h.customer_price_usd)} · {h.margin_percent}% margin
                  </span>
                </li>
              ))}
            </motion.ul>
          )}
        </AnimatePresence>
      </div>
    </Panel>
  )
}

function MoneyInput({
  value,
  onChange,
  placeholder
}: {
  value: string
  onChange: (v: string) => void
  placeholder?: string
}) {
  return (
    <div className="relative">
      <span className="pointer-events-none absolute left-3.5 top-1/2 -translate-y-1/2 text-sm text-muted">$</span>
      <input
        className={cn(inputClass, 'pl-7')}
        type="number"
        min={0}
        value={value}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
      />
    </div>
  )
}

function MarginBar({
  price,
  cost,
  floor
}: {
  price: number | null
  cost: number | null
  floor?: number
}) {
  if (price == null || price <= 0 || cost == null) {
    return (
      <div className="rounded-lg border border-dashed border-border px-3 py-3 text-xs text-muted">
        Enter a price and a margin or cost to see the split.
      </div>
    )
  }
  const costPct = Math.min(100, Math.max(0, (cost / price) * 100))
  const marginPct = 100 - costPct
  const low = floor != null && marginPct < floor
  return (
    <div>
      <div className="relative flex h-3 overflow-hidden rounded-full bg-background-elevated">
        <motion.div
          className="h-full bg-slate-400/60"
          animate={{ width: `${costPct}%` }}
          transition={{ type: 'spring', stiffness: 180, damping: 24 }}
        />
        <motion.div
          className={cn('h-full', low ? 'bg-error' : 'bg-positive')}
          animate={{ width: `${marginPct}%` }}
          transition={{ type: 'spring', stiffness: 180, damping: 24 }}
        />
        {floor != null && (
          <span
            className="absolute top-0 h-full w-0.5 bg-primary/70"
            style={{ left: `${100 - floor}%` }}
            title={`${floor}% margin floor`}
          />
        )}
      </div>
      <div className="mt-1.5 flex justify-between text-[11px] text-muted">
        <span>Cepoch cost {usd(cost)}</span>
        <span className={low ? 'font-semibold text-error' : 'font-semibold text-positive'}>
          Margin {usd(price - cost)} · {marginPct.toFixed(1)}%
        </span>
      </div>
    </div>
  )
}

/* ------------------------------------------------------------------ scope weights */

function ScopeWeights({
  rules,
  config,
  onSaved
}: {
  rules: Rules
  config: ScopeConfig
  onSaved: () => Promise<void>
}) {
  const [addons, setAddons] = useState<Record<string, string>>({})
  const [factors, setFactors] = useState<Record<string, string>>({})
  const [included, setIncluded] = useState('')
  const [integration, setIntegration] = useState('')
  const [rounding, setRounding] = useState('')
  const [floor, setFloor] = useState('')
  const [cap, setCap] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [flash, setFlash] = useState<string | null>(null)

  useEffect(() => {
    const s = rules.scope_rules
    setAddons(
      Object.fromEntries(config.addon_domains.map((d) => [d, s.addon_usd[d] != null ? String(s.addon_usd[d]) : '']))
    )
    setFactors(Object.fromEntries(AUTONOMY.map((a) => [a.key, String(s.autonomy_factor[a.key] ?? 1)])))
    setIncluded(String(s.integrations_included))
    setIntegration(String(s.integration_usd))
    setRounding(String(s.round_to_usd))
    setFloor(String(s.min_margin_percent))
    setCap(String(s.max_discount_percent))
  }, [rules, config])

  async function save() {
    setBusy(true)
    setError(null)
    setFlash(null)
    try {
      await api.put('/api/pricing/resale-rules', {
        scope_rules: {
          addon_usd: Object.fromEntries(
            Object.entries(addons)
              .filter(([, v]) => v.trim() !== '')
              .map(([k, v]) => [k, Number(v)])
          ),
          autonomy_factor: Object.fromEntries(Object.entries(factors).map(([k, v]) => [k, Number(v)])),
          integrations_included: Number(included),
          integration_usd: Number(integration),
          round_to_usd: Number(rounding),
          min_margin_percent: Number(floor),
          max_discount_percent: Number(cap)
        }
      })
      setFlash('Scope rules saved — the calculator uses them now.')
      await onSaved()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Save failed')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Panel
      title="Scope rules"
      description="What each add-on, autonomy level and integration adds. Leave an add-on empty to not offer it."
    >
      {!rules.scope_rules.customized && (
        <p className="mb-3 rounded-lg border border-brand/30 bg-brand/10 px-3 py-2 text-xs text-brand-soft">
          These are starting face values. Change them to your numbers and save.
        </p>
      )}
      {error && <div className="mb-3"><ErrorNote>{error}</ErrorNote></div>}
      {!rules.configured && (
        <p className="mb-3 text-xs text-muted">Save a base price first — scope rules sit on top of it.</p>
      )}
      <div className="space-y-4">
        <div>
          <p className="type-caption mb-2">Add-on domains</p>
          <div className="grid gap-2 sm:grid-cols-2">
            {config.addon_domains.map((d) => (
              <label key={d} className="flex items-center gap-2 rounded-lg border border-border bg-background-elevated px-3 py-2">
                <span className="w-20 text-sm font-medium capitalize text-primary">{d}</span>
                <div className="flex-1">
                  <MoneyInput
                    value={addons[d] ?? ''}
                    onChange={(v) => setAddons((prev) => ({ ...prev, [d]: v }))}
                    placeholder="not offered"
                  />
                </div>
              </label>
            ))}
          </div>
        </div>
        <div>
          <p className="type-caption mb-2">Autonomy multiplier</p>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            {AUTONOMY.map((a) => (
              <label key={a.key} className="rounded-lg border border-border bg-background-elevated px-3 py-2">
                <span className="block text-xs font-medium text-primary">{a.label}</span>
                <div className="relative mt-1">
                  <span className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-sm text-muted">×</span>
                  <input
                    className={cn(inputClass, 'min-h-9 pl-7')}
                    type="number"
                    step={0.01}
                    value={factors[a.key] ?? ''}
                    onChange={(e) => setFactors((prev) => ({ ...prev, [a.key]: e.target.value }))}
                  />
                </div>
              </label>
            ))}
          </div>
        </div>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Integrations included">
            <input className={inputClass} type="number" min={0} value={included} onChange={(e) => setIncluded(e.target.value)} />
          </Field>
          <Field label="Each extra integration">
            <MoneyInput value={integration} onChange={setIntegration} />
          </Field>
          <Field label="Round quotes up to" hint="0 keeps exact amounts">
            <MoneyInput value={rounding} onChange={setRounding} />
          </Field>
          <Field label="Margin floor %" hint="Quotes under this cannot be saved">
            <input className={inputClass} type="number" min={0} max={95} value={floor} onChange={(e) => setFloor(e.target.value)} />
          </Field>
          <Field label="Max discount %">
            <input className={inputClass} type="number" min={0} max={90} value={cap} onChange={(e) => setCap(e.target.value)} />
          </Field>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <Btn size="sm" loading={busy} disabled={!rules.configured} onClick={() => void save()}>
            <Save className="h-4 w-4" /> Save scope rules
          </Btn>
          {flash && <span className="text-xs text-positive">{flash}</span>}
        </div>
      </div>
    </Panel>
  )
}

/* ------------------------------------------------------------------ calculator */

function ConsultCalculator({
  rules,
  config,
  loaded,
  onSaved
}: {
  rules: Rules
  config: ScopeConfig
  loaded: SavedQuote | null
  onSaved: () => Promise<void>
}) {
  const [addons, setAddons] = useState<string[]>([])
  const [autonomy, setAutonomy] = useState('WAIT_FOR_APPROVAL')
  const [integrations, setIntegrations] = useState(2)
  const [discount, setDiscount] = useState(0)
  const [label, setLabel] = useState('')
  const [quote, setQuote] = useState<Quote | null>(null)
  const [calcError, setCalcError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [flash, setFlash] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)
  const seq = useRef(0)

  useEffect(() => {
    if (!loaded) return
    setAddons(loaded.inputs.addons)
    setAutonomy(loaded.inputs.autonomy)
    setIntegrations(loaded.inputs.integrations)
    setDiscount(loaded.inputs.discount_percent || 0)
    setLabel(loaded.label)
  }, [loaded])

  const offered = config.addon_domains.filter((d) => config.addon_usd[d] != null)

  useEffect(() => {
    setAddons((prev) => prev.filter((d) => config.addon_usd[d] != null))
    setDiscount((prev) => Math.min(prev, config.max_discount_percent))
  }, [config])

  const body = useMemo(
    () => ({
      domains: [...config.base_package_domains, ...addons],
      autonomy,
      integrations,
      discount_percent: discount
    }),
    [config.base_package_domains, addons, autonomy, integrations, discount]
  )

  useEffect(() => {
    if (!rules.configured) return
    const id = ++seq.current
    const timer = setTimeout(async () => {
      try {
        const res = await api.post<{ quote: Quote }>('/api/pricing/consult-quote', { ...body, save: false })
        if (id === seq.current) {
          setQuote(res.quote)
          setCalcError(null)
        }
      } catch (err) {
        if (id === seq.current) setCalcError(err instanceof Error ? err.message : 'Could not calculate')
      }
    }, 180)
    return () => clearTimeout(timer)
  }, [body, rules])

  async function save() {
    setBusy(true)
    setFlash(null)
    try {
      await api.post('/api/pricing/consult-quote', { ...body, label: label || 'Consult quote', save: true })
      setFlash('Quote saved.')
      await onSaved()
    } catch (err) {
      setCalcError(err instanceof Error ? err.message : 'Save failed')
    } finally {
      setBusy(false)
    }
  }

  async function copy() {
    if (!quote) return
    await navigator.clipboard.writeText(quote.talk_track)
    setCopied(true)
    setTimeout(() => setCopied(false), 1500)
  }

  if (!rules.configured) {
    return (
      <Panel title="Consult calculator" description="Price the scope live on a call.">
        <div className="rounded-xl border border-dashed border-border px-6 py-10 text-center text-sm text-muted">
          Set a base price above and the calculator switches on.
        </div>
      </Panel>
    )
  }

  return (
    <Panel
      title="Consult calculator"
      description="Pick the scope while you talk — price, margin and the words to say update instantly."
    >
      <div className="grid gap-6 lg:grid-cols-[1.1fr_1fr]">
        <div className="space-y-5">
          <div>
            <p className="type-caption mb-2">Included in the base package</p>
            <div className="flex flex-wrap gap-1.5">
              {config.base_package_domains.map((d) => (
                <span key={d} className="inline-flex items-center gap-1 rounded-full bg-positive/10 px-2.5 py-1 text-xs font-medium capitalize text-positive">
                  <Check className="h-3 w-3" /> {d}
                </span>
              ))}
            </div>
          </div>

          <div>
            <p className="type-caption mb-2">Add-ons</p>
            {offered.length === 0 ? (
              <p className="text-xs text-muted">No add-ons are priced for this client.</p>
            ) : (
              <div className="grid gap-2 sm:grid-cols-2">
                {offered.map((d) => {
                  const active = addons.includes(d)
                  return (
                    <button
                      key={d}
                      type="button"
                      aria-pressed={active}
                      onClick={() =>
                        setAddons((prev) => (prev.includes(d) ? prev.filter((x) => x !== d) : [...prev, d]))
                      }
                      className={cn(
                        'flex items-center justify-between rounded-xl border px-4 py-3 text-left ui-transition',
                        active
                          ? 'border-brand bg-brand/10 shadow-sm'
                          : 'border-border bg-background-elevated hover:border-slate-300'
                      )}
                    >
                      <span className="flex items-center gap-2">
                        <span
                          className={cn(
                            'flex h-5 w-5 items-center justify-center rounded-md border ui-transition',
                            active ? 'border-brand bg-brand text-white' : 'border-border'
                          )}
                        >
                          {active && <Check className="h-3 w-3" />}
                        </span>
                        <span className="text-sm font-medium capitalize text-primary">{d}</span>
                      </span>
                      <span className="text-xs font-semibold text-secondary">+{usd(config.addon_usd[d])}</span>
                    </button>
                  )
                })}
              </div>
            )}
          </div>

          <div>
            <p className="type-caption mb-2">Autonomy</p>
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
              {AUTONOMY.map((a) => {
                const active = autonomy === a.key
                return (
                  <button
                    key={a.key}
                    type="button"
                    aria-pressed={active}
                    onClick={() => setAutonomy(a.key)}
                    className={cn(
                      'rounded-xl border px-3 py-2.5 text-left ui-transition',
                      active ? 'border-brand bg-brand/10 shadow-sm' : 'border-border bg-background-elevated hover:border-slate-300'
                    )}
                  >
                    <span className="block text-sm font-semibold text-primary">{a.label}</span>
                    <span className="block text-[11px] text-muted">{a.hint}</span>
                    <span className="mt-1 block text-[11px] font-semibold text-brand">
                      × {config.autonomy_factor[a.key]}
                    </span>
                  </button>
                )
              })}
            </div>
          </div>

          <div className="grid gap-4 sm:grid-cols-2">
            <div>
              <p className="type-caption mb-2">Integrations</p>
              <div className="flex items-center gap-2">
                <Btn variant="secondary" size="sm" aria-label="Fewer" onClick={() => setIntegrations((n) => Math.max(0, n - 1))}>
                  <Minus className="h-4 w-4" />
                </Btn>
                <span className="w-10 text-center font-geist text-xl font-semibold text-primary">{integrations}</span>
                <Btn variant="secondary" size="sm" aria-label="More" onClick={() => setIntegrations((n) => Math.min(50, n + 1))}>
                  <Plus className="h-4 w-4" />
                </Btn>
              </div>
              <p className="mt-1.5 text-[11px] text-muted">
                {config.integrations_included} included, then {usd(config.integration_usd)} each
              </p>
            </div>
            <div>
              <p className="type-caption mb-2">
                Discount <span className="font-semibold text-primary">{discount}%</span>
              </p>
              <input
                type="range"
                min={0}
                max={config.max_discount_percent}
                step={0.5}
                value={discount}
                onChange={(e) => setDiscount(Number(e.target.value))}
                className="w-full accent-[rgb(var(--color-brand))]"
                aria-label="Discount"
              />
              <p className="mt-1.5 text-[11px] text-muted">Up to {config.max_discount_percent}% by your rules</p>
            </div>
          </div>
        </div>

        <div className="lg:sticky lg:top-4 lg:self-start">
          <div className="overflow-hidden rounded-2xl border border-border bg-background-secondary shadow-sm">
            <div className={cn('px-5 py-5', quote?.below_floor ? 'bg-error/5' : 'bg-gradient-to-br from-brand/15 to-transparent')}>
              <div className="flex items-center justify-between">
                <p className="type-caption">Customer price</p>
                <Badge tone={quote?.placeholder ? 'warn' : 'good'}>
                  {quote?.placeholder ? 'Working number' : 'Venu confirmed'}
                </Badge>
              </div>
              <AnimatePresence mode="wait">
                <motion.p
                  key={quote?.customer_price_usd ?? 'none'}
                  initial={{ opacity: 0, y: 6 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, y: -6 }}
                  transition={{ duration: 0.15 }}
                  className="mt-1 font-geist text-4xl font-semibold tracking-tight text-primary"
                >
                  {quote ? usd(quote.customer_price_usd) : '—'}
                </motion.p>
              </AnimatePresence>
              <p className="mt-1 text-xs text-muted">One custom engagement price — not per seat.</p>
              <div className="mt-4">
                <MarginBar
                  price={quote?.customer_price_usd ?? null}
                  cost={quote?.cepoch_cost_usd ?? null}
                  floor={quote?.min_margin_percent}
                />
              </div>
            </div>

            <div className="space-y-4 px-5 py-4">
              {calcError && <ErrorNote>{calcError}</ErrorNote>}
              {quote && (
                <>
                  <ul className="space-y-1.5 text-sm">
                    {quote.lines.map((line, i) => (
                      <li key={`${line.kind}-${line.domain}-${i}`} className="flex items-baseline justify-between gap-3">
                        <span className="text-secondary">
                          {line.label}
                          {line.detail && <span className="ml-1.5 text-[11px] text-muted">{line.detail}</span>}
                        </span>
                        <span className={cn('font-geist font-medium tabular-nums', line.customer_usd < 0 ? 'text-error' : 'text-primary')}>
                          {line.customer_usd < 0 ? '−' : ''}
                          {usd(Math.abs(line.customer_usd), true)}
                        </span>
                      </li>
                    ))}
                    <li className="flex justify-between border-t border-border pt-2 font-semibold text-primary">
                      <span>Total</span>
                      <span className="font-geist tabular-nums">{usd(quote.customer_price_usd, true)}</span>
                    </li>
                  </ul>

                  {quote.warnings.length > 0 && (
                    <ul className="space-y-1">
                      {quote.warnings.map((w) => (
                        <li
                          key={w}
                          className={cn(
                            'flex items-start gap-1.5 text-xs',
                            w.includes('floor') || w.includes('old package') ? 'text-error' : 'text-warning'
                          )}
                        >
                          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" /> {w}
                        </li>
                      ))}
                    </ul>
                  )}

                  <div className="rounded-xl border border-brand/20 bg-brand/5 p-3">
                    <div className="mb-1.5 flex items-center justify-between">
                      <p className="flex items-center gap-1.5 text-xs font-semibold text-brand">
                        <Sparkles className="h-3.5 w-3.5" /> What to say
                      </p>
                      <Btn variant="ghost" size="sm" className="min-h-7 px-2" onClick={() => void copy()}>
                        {copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
                        {copied ? 'Copied' : 'Copy'}
                      </Btn>
                    </div>
                    <p className="text-sm leading-relaxed text-primary">{quote.talk_track}</p>
                  </div>

                  <div className="flex gap-2">
                    <input
                      className={inputClass}
                      value={label}
                      onChange={(e) => setLabel(e.target.value)}
                      placeholder="Label, e.g. Acme — call with CFO"
                    />
                    <Btn size="md" loading={busy} disabled={quote.below_floor} onClick={() => void save()}>
                      <Save className="h-4 w-4" /> Save
                    </Btn>
                  </div>
                  {flash && <p className="text-xs text-positive">{flash}</p>}
                </>
              )}
            </div>
          </div>
        </div>
      </div>
    </Panel>
  )
}

/* ------------------------------------------------------------------ saved */

function SavedQuotes({ items, onLoad }: { items: SavedQuote[]; onLoad: (q: SavedQuote) => void }) {
  if (items.length === 0) return null
  return (
    <Panel title="Saved quotes" description="Load one back into the calculator to adjust it.">
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border text-left text-xs text-muted">
              <th className="py-2 pr-3 font-medium">Quote</th>
              <th className="py-2 pr-3 font-medium">Scope</th>
              <th className="py-2 pr-3 text-right font-medium">Price</th>
              <th className="py-2 pr-3 text-right font-medium">Margin</th>
              <th className="py-2 pr-3 font-medium">Saved</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {items.map((q) => (
              <tr key={q.id} className="border-b border-border/60 last:border-0">
                <td className="py-2.5 pr-3 font-medium text-primary">
                  {q.label}
                  {q.placeholder && <Badge tone="warn" className="ml-2">working</Badge>}
                </td>
                <td className="py-2.5 pr-3 text-xs text-muted">
                  Base{q.inputs.addons.length ? ` + ${q.inputs.addons.join(', ')}` : ''} · {q.inputs.autonomy.toLowerCase().replace(/_/g, ' ')} ·{' '}
                  {q.inputs.integrations} integrations
                  {q.inputs.discount_percent ? ` · ${q.inputs.discount_percent}% off` : ''}
                </td>
                <td className="py-2.5 pr-3 text-right font-geist font-semibold tabular-nums text-primary">
                  {usd(q.customer_price_usd)}
                </td>
                <td className="py-2.5 pr-3 text-right tabular-nums text-secondary">{q.margin_percent}%</td>
                <td className="py-2.5 pr-3 text-xs text-muted">{timestamp(q.saved_at)}</td>
                <td className="py-2.5 text-right">
                  <Btn variant="ghost" size="sm" onClick={() => onLoad(q)}>
                    Load
                  </Btn>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  )
}
