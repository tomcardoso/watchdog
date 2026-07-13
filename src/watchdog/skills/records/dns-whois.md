---
description: a WHOIS registration record, DNS zone file excerpt, domain registration history, IP address allocation, or related internet infrastructure record
---
# Domain knowledge — DNS and WHOIS records

This skill is loaded by Watchdog when the document type is a WHOIS registration record, DNS zone file excerpt, domain registration history, IP address allocation, or related internet infrastructure record.

---

## Document types covered

- WHOIS domain registration records (current and historical)
- RDAP (Registration Data Access Protocol) responses
- Historical WHOIS data
- DNS zone files and DNS record exports
- Passive DNS data
- IP address WHOIS / ARIN / RIPE / APNIC allocation records
- SSL/TLS certificate transparency logs
- Domain registration screenshots
- Name server history records
- BGP routing data

---

## Fields to extract

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

- **Domain registered shortly before its first known use** — record the registration/creation date. When it sits just before a launch, announcement, or campaign named elsewhere in the vault, flag the correlation as a lead; the date is a fact, tying it to an event is for the reporter to check.
- **Privacy protection masking a registrant** — privacy services (WhoisGuard, DomainsByProxy, Withheld for Privacy) replace registrant details, and their name is visible in the record. Their presence is not suspicious in itself; flag it when the digest shows the entity behind the domain is one that claims transparency (a government body, a public company).
- **Registrant contact shared with an entity already in the vault** — a registrant email, phone, or address that matches a value recorded on an existing entity is the strongest link between a domain and its owner. Compare the contact fields against the entity digest and record any match.
- **Name server clustering** — domains that share a name server may be managed by the same operator even when registrant details differ. Record the name servers and flag a match against name servers already in the digest.
- **Look-alike / impersonation domains** — a domain whose name mimics a known organization (governmentofcanada.com, cbc-news.ca) may be used for phishing, fraud, or impersonation. This is visible in the domain string itself.
- **Registrant or transfer change in the record's history** — when the document's own history shows a change of registrant or a transfer, record it; note it as a lead if the change precedes other activity you would want to date.

### Infrastructure connections

- **Shared IP hosting** — multiple domains resolving to the same IP address. Legitimate shared hosting is common, so record the shared IP and flag it only when it is combined with a registrant or name-server match against the digest.
- **SSL certificate Subject Alternative Names (SANs)** — a single certificate often covers multiple domains. The SAN list names other domains served from the same infrastructure; capture every domain listed.
- **IP owner / ASN as stated** — capture the IP owner and ASN recorded in the document. Whether that network operator is itself significant (a state entity, a known-abusive host) is an outside-knowledge question — log it as a lead rather than a finding.

### Historical patterns

- **Domain dropped and re-registered** — when the record's history shows the domain lapsed and was re-registered by a different party, capture it; the new owner may be exploiting the old domain's reputation or traffic.
- **New certificate or recent activation** — a certificate-transparency entry or name-server change shown in the record signals the site was recently activated or relaunched; record the date.

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
| **RIR** | Regional Internet Registry — one of five bodies that allocate IP addresses and ASNs for a world region (ARIN, RIPE NCC, APNIC, LACNIC, AFRINIC) |
| **ARIN** | American Registry for Internet Numbers — allocates IP addresses for the US, Canada, and parts of the Caribbean |
| **RIPE NCC** | Regional Internet Registry for Europe, the Middle East, and Central Asia |
| **APNIC** | Asia-Pacific Network Information Centre — allocates IP addresses for the Asia-Pacific region |
| **LACNIC** | Latin America and Caribbean Network Information Centre — allocates IP addresses for Latin America and the Caribbean |
| **AFRINIC** | African Network Information Centre — allocates IP addresses for Africa |
| **ASN** | Autonomous System Number — identifies a network under a single routing policy |
| **Passive DNS** | A database of historical DNS resolutions — shows what IP a domain pointed to in the past |
| **Name server** | The authoritative server that answers DNS queries for a domain; changing name servers is a common infrastructure migration signal |
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

1. **Historical WHOIS data** — current WHOIS records are often privacy-protected, but databases like DomainTools and SecurityTrails retain historical records, which may contain real registrant details.
2. **Certificate transparency as a discovery tool** — crt.sh logs every SSL certificate issued. Searching for a company name or domain in CT logs reveals all domains they have secured SSL certificates for, including subdomains and related domains not otherwise public.
3. **Name server as a pivot** — when registrant details are hidden, the name server may be a useful pivot. Domains sharing a custom name server (e.g. ns1.companysecretproject.com) may be controlled by the same entity.
4. **Subdomain enumeration** — the main domain is often just the surface. Subdomains (admin.example.com, api.example.com) may expose infrastructure, internal tools, or related properties. Certificate transparency logs are the best source for subdomain discovery.
5. **ARIN / RIPE search for IP ownership** — an IP address can be searched in ARIN or RIPE to find who owns the netblock. A netblock allocated to a foreign state entity or an unknown private company when a legitimate business is expected is a red flag.
6. **BGP routing history** — BGP routing data shows which ASN announced a given IP prefix and when. Hijacking of IP space (a relatively rare but documented attack) appears as a sudden change in which ASN is announcing a prefix.
7. **Bulletproof hosting and abusive networks** — some hosting providers and ASNs are known to ignore abuse complaints and shelter fraud, spam, and malware operations. An IP or ASN in the record can be checked against OSINT abuse databases (abuse.ch, Shodan) to see whether the operator has a history of tolerating abuse — a lookup the extractor cannot do, so flag the IP/ASN as a lead.

---

## Sources and further reading

### Official and regulatory

- [ICANN Lookup — lookup.icann.org](https://lookup.icann.org) — ICANN's authoritative WHOIS and RDAP lookup tool for current domain registration records across all TLDs
- [ARIN — American Registry for Internet Numbers](https://www.arin.net) — Nonprofit that administers IP addresses and ASNs for Canada, the US, and many Caribbean and North Atlantic territories; use for IP ownership lookups
- [RIPE NCC](https://www.ripe.net) — Regional Internet Registry for Europe, the Middle East, and Central Asia; provides IP allocation data and network operator information for those regions

### Practitioner and public interest

- [DomainTools](https://www.domaintools.com) — Commercial platform with over two decades of historical WHOIS and passive DNS data; the primary tool for pivoting on shared registrant emails, name servers, and IP history
- [SecurityTrails](https://securitytrails.com) — Historical DNS, WHOIS, and subdomain data; a common alternative to DomainTools for infrastructure history and domain clustering
- [crt.sh — Certificate Transparency search](https://crt.sh) — Free search interface over certificate transparency logs; the primary tool for discovering subdomains and related domains through issued SSL/TLS certificates
- [Shodan](https://www.shodan.io) — Search engine for internet-connected devices; use to identify what services and infrastructure are exposed on a given IP address or network range
- [abuse.ch](https://abuse.ch) — Nonprofit that tracks malware, botnet, and abusive-hosting infrastructure; useful for checking whether an IP or ASN is associated with known abuse

### Journalism resources

- [Bellingcat's Online Investigation Toolkit](https://bellingcat.gitbook.io/toolkit) — Community-maintained catalogue of OSINT tools, including domain, DNS, and certificate-lookup resources used in published investigations
- [OSINT Framework](https://osintframework.com) — Categorized directory of open-source intelligence tools, with dedicated sections for domain names, IP addresses, and DNS

**Notes on unsourced claims:** The insight on "bulletproof hosting and abusive networks" (under *What investigators typically miss*) relies on practitioner knowledge and OSINT community databases (Shodan, abuse.ch) rather than a single citable source. That such providers tolerate abuse is well-documented in cybersecurity literature, but no single authoritative public reference was identified.
