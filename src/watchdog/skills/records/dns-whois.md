# Domain knowledge — DNS and WHOIS records

This skill is loaded by `/ingest` when the document type is a WHOIS registration record, DNS zone file excerpt, domain registration history, IP address allocation, or related internet infrastructure record.

Apply this knowledge in addition to the standard extraction process. It tells you what to look for, what terminology means, and what patterns are worth flagging.

---

## Document types covered

- WHOIS domain registration records (current and historical)
- RDAP (Registration Data Access Protocol) responses
- Historical WHOIS data (via passive DNS databases)
- DNS zone files and DNS record exports
- Passive DNS data
- IP address WHOIS / ARIN / RIPE / APNIC allocation records
- SSL/TLS certificate transparency logs
- Domain registration screenshots
- Name server history records
- BGP routing data

---

## Always-present fields to extract

| Field | What to look for |
|-------|-----------------|
| **Domain name** | The registered domain (e.g. example.com) |
| **Registrar** | The company through which the domain was registered |
| **Registration date** | When the domain was first registered |
| **Expiry date** | When the domain registration expires |
| **Last updated** | When the registration record was last modified |
| **Registrant name** | Name of the domain owner (may be redacted or privacy-protected) |
| **Registrant organization** | Organization associated with the registration |
| **Registrant email** | Contact email (often masked by a privacy service) |
| **Registrant address** | Physical address (often masked) |
| **Name servers** | The authoritative DNS servers for the domain |
| **IP address(es)** | Where the domain resolves to |
| **IP owner / ASN** | Who owns the IP address and autonomous system |
| **SSL certificate details** | Issued to, issued by, validity period, Subject Alternative Names |

---

## Red flags — what to look for

### Registration patterns

- **Domain registered shortly before a significant event** — a domain registered days or weeks before a company's public launch, a political campaign announcement, or a disinformation campaign is worth noting. Registration date is often the clearest indicator of intent.
- **Privacy protection masking a registrant** — privacy services (WhoisGuard, DomainsByProxy, Withheld for Privacy) hide registrant details. Their presence is not suspicious in itself, but their use by entities that claim transparency (government agencies, public companies) is inconsistent.
- **Same registrant email or address across multiple domains** — a shared contact field is the most common way to cluster domains owned by the same person or organization. Historical WHOIS databases often retain pre-privacy-protection data.
- **Name server clustering** — domains that share name servers are often managed by the same organization even when registrant details differ. This is a powerful linking technique.
- **Domain squatting patterns** — a portfolio of domains that mimic legitimate organizations (governmentofcanada.com, cbc-news.ca) may be used for phishing, fraud, or impersonation.
- **Rapid transfer of registration** — a domain transferred to a new registrant shortly before it was used in a suspicious way.

### Infrastructure connections

- **Shared IP hosting** — multiple domains resolving to the same IP address. Legitimate shared hosting is common; the combination of shared hosting and shared registrant details strengthens an attribution.
- **Bulletproof hosting providers** — certain hosting providers are known to tolerate abuse. An IP address registered to a known bulletproof provider (identified by OSINT databases) is a red flag.
- **ASN ownership** — autonomous system numbers identify the network operator. A domain resolving to an IP owned by a foreign state-controlled entity or a known criminal network is significant.
- **SSL certificate Subject Alternative Names (SANs)** — a single SSL certificate often covers multiple domains. SANs in the certificate reveal other domains served from the same infrastructure.

### Historical patterns

- **Domain dropped and re-registered** — a domain that lapsed and was re-registered by a new party; the new owner may be exploiting the old domain's reputation or traffic.
- **Change in registrant shortly before suspicious activity** — a domain used legitimately for years, then transferred to a new registrant who changes the content to disinformation or fraud.
- **Certificate transparency log entries** — certificate transparency logs record every SSL certificate issued. A new certificate for a domain is a signal the site was recently activated or relaunched.

---

## Terminology

| Term | Meaning |
|------|---------|
| **WHOIS** | A protocol and database for querying domain and IP registration records |
| **RDAP** | Registration Data Access Protocol — the modern replacement for WHOIS, with structured JSON responses |
| **Registrar** | The company (e.g. GoDaddy, Namecheap) through which a domain is registered |
| **Registry** | The organization that manages a top-level domain (e.g. Verisign for .com, CIRA for .ca) |
| **CIRA** | Canadian Internet Registration Authority — manages .ca domains |
| **ICANN** | Internet Corporation for Assigned Names and Numbers — governs the global domain name system |
| **ARIN** | American Registry for Internet Numbers — allocates IP addresses for Canada and the US |
| **RIPE NCC** | Europe's IP registry — allocates IP addresses for Europe and much of the Middle East |
| **ASN** | Autonomous System Number — identifies a network under a single routing policy |
| **Passive DNS** | A database of historical DNS resolutions — shows what IP a domain pointed to in the past |
| **Name server** | The server that translates a domain name to an IP address; changing name servers is a common infrastructure migration signal |
| **Certificate transparency (CT) log** | A public log of all SSL/TLS certificates issued; browsable at crt.sh |
| **Privacy proxy / WHOIS privacy** | A service that replaces registrant details with the proxy provider's details to obscure the true owner |
| **Bulletproof hosting** | Hosting providers that ignore abuse complaints — used by fraud, spam, and malware operations |

---

## Relationships to extract from DNS and WHOIS records

1. **Person/Organization → Domain**: Registrant (with registration and expiry dates)
2. **Domain → IP address**: Resolution (current and historical)
3. **Domain → Name server**: DNS infrastructure (shared name servers link domains)
4. **IP address → ASN/Organization**: Network owner
5. **Domain → SSL certificate**: Issued to/by, validity, Subject Alternative Names (links to co-hosted domains)
6. **Domain → Domain**: Shared registrant, IP, or name server (infrastructure clustering)

---

## What investigators typically miss

1. **Historical WHOIS data** — current WHOIS records are often privacy-protected, but databases like DomainTools, SecurityTrails, and Spyse retain historical records from before privacy protection was applied. These often contain real registrant details.
2. **Certificate transparency as a discovery tool** — crt.sh logs every SSL certificate issued. Searching for a company name or domain in CT logs reveals all domains they have secured SSL certificates for, including subdomains and related domains not otherwise public.
3. **Name server as a pivot** — when registrant details are hidden, the name server is often the best pivot. Domains sharing a custom name server (e.g. ns1.companysecretproject.com) are almost certainly controlled by the same entity.
4. **Subdomain enumeration** — the main domain is often just the surface. Subdomains (admin.example.com, api.example.com) may expose infrastructure, internal tools, or related properties. Certificate transparency logs are the best source for subdomain discovery.
5. **ARIN / RIPE search for IP ownership** — an IP address can be searched in ARIN or RIPE to find who owns the netblock. A netblock allocated to a foreign state entity or an unknown private company when a legitimate business is expected is a red flag.
6. **BGP routing history** — BGP routing data shows which ASN announced a given IP prefix and when. Hijacking of IP space (a relatively rare but documented attack) appears as a sudden change in which ASN is announcing a prefix.
