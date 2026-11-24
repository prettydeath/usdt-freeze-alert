import { useEffect, useState } from "react"
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog"

const G: [string, string][] = [
  ["Freeze / blacklist", "Tether (or Circle) blocks an address: it can no longer send the token. Funds stay on the address."],
  ["Unfreeze", "The block is lifted."],
  ["Destroy (confiscation)", 'The whole balance of an already-frozen address is burned. The funds are gone, which is why it is shown as "burned".'],
  ["Issue / mint", "New tokens are created to Tether's treasury (supply goes up). Not related to freezes."],
  ["Redeem / burn", "Tokens are removed from Tether's treasury (supply goes down). Not related to freezes."],
  ["Proposal, multisig, signatures x/y", 'Tether\'s token owner is a multisig wallet: an action is first proposed by one signer, then needs x of y signatures before it executes. "1/2 signed" means one more signature is needed.'],
  ["Safe API vs on-chain queue", "Most chains show a pending proposal on-chain. USDT0 chains use a Safe wallet whose queue lives off-chain in the Safe service, and signers may skip it, so some freezes are never announced."],
  ["No public proposal", "The freeze was found on-chain but no matching published Safe proposal exists, so its delay is unknown."],
  ["No advance notice", "The action executed in the same moment it was proposed (delay 0), so nobody could see it coming."],
  ["Delay", "Time from the proposal being submitted to the action being executed. On TRON the execution time is inferred from the confirmations."],
  ["Stale", "A proposal older than 7 days that never executed. Funds may still be on the address."],
  ["Cross-chain signal", 'An address frozen (or proposed for freezing) on one chain that still holds at least $1,000 on another chain where it is not blocked. The lag is the time until the second chain blocks it; "moved before" means its balance there dropped by more than 10% first.'],
  ["Moved out before the freeze", 'Token transfers from the address in the 72 hours before the freeze executed. "In-window" = between the proposal being submitted and executed. Recipients are labelled from wallet-attribution where known.'],
  ["Cluster", "Batch: freezes on one chain within 10 minutes of each other. Cross-chain: one address frozen on several chains. Counterparty: frozen addresses sharing senders or recipients (labelled exchanges, bridges, protocols and busy hubs are ignored as connectors). A cluster is a lead, not proof of a common owner."],
  ["Reason", 'Why an address was frozen is rarely public. A reason is shown only from a curated entry with a public source (config/reasons.yaml) or as a hint from a wallet-attribution label (hack, scam, ransomware, sanctions ...), always with its source link. "Unknown" means no public reason is known to this tool.'],
  ["OFAC SDN", 'The address is on the current OFAC SDN list of digital-currency addresses, according to a wallet-attribution label whose sources include ofac-sdn. Other "sanctioned" labels without that source are shown as hints only.'],
  ["Pulse", "Executions by hour and weekday (UTC), how long each signer takes to confirm, how often a signer gives the last signature, the monthly median delay per chain, and the number of open proposals over time."],
  ["Address page", "#addr=<address> opens a shareable full page for one address with a copyable Markdown report (.md / .json download, or Print to PDF)."],
  ["Labels", 'Entity labels come from the public wallet-attribution dataset (MIT) and its upstream sources (DefiLlama CEX lists, GraphSense, OFAC SDN and others). They are shown as "label from wallet-attribution", never as our own claim.'],
  ["Sinks (excluded)", "Zero address and token-contract addresses are blacklisted to burn mistaken sends; they are excluded from totals and lists."],
  ["USDT, USDT0, pre-USDT0 Arbitrum", 'USDT is Tether\'s own token; USDT0 is its omnichain version controlled by a different Safe wallet. On Arbitrum the token changed hands on 2025-01-28: earlier actions were done by Tether\'s multisig (shown as "Arbitrum USDT"), later ones by the USDT0 Safe.'],
]

export function GlossaryDialog() {
  const [open, setOpen] = useState(false)
  useEffect(() => {
    const f = () => setOpen(true)
    window.addEventListener("ufa:glossary", f)
    return () => window.removeEventListener("ufa:glossary", f)
  }, [])
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogContent>
        <DialogTitle>Glossary</DialogTitle>
        <DialogDescription>Terms used on this dashboard</DialogDescription>
        <dl className="text-[13px]">
          {G.map(([t, x]) => <div key={t} className="mt-2.5"><dt className="font-bold">{t}</dt><dd className="text-muted-foreground m-0">{x}</dd></div>)}
        </dl>
      </DialogContent>
    </Dialog>
  )
}
