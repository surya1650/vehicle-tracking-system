# Requesting registry access through API Setu

Use the organisation's account to request access as an API consumer. Search the catalogue for **VAHAN**, **Vehicle Registration Certificate (RC)**, and **Pollution Under Control Certificate (PUCC)**. Start with the Ministry of Road Transport and Highways or the listed publisher. Ask API Setu which publisher or approval route handles PUCC if a matching service is unavailable to your account. Service availability and field permissions depend on the publisher and the organisation's intended use. [API Setu](https://apisetu.gov.in/)

## Message to send

> We operate an office vehicle entry, exit, and parking portal. We request sandbox access for authorised vehicle checks to VAHAN Vehicle Registration Certificate details and Parivahan Pollution Under Control Certificate details or validity. We need the vehicle make, model, fuel type, registration validity, and PUCC certificate number and expiry. Please confirm eligibility, consent or verification requirements, permitted fields, and storage terms, and share the sandbox endpoints, API documentation, and authentication process.

Ask separately whether insurance and fitness validity are available to your organisation. The portal can use the owner or department from your existing site records. Request owner information only when needed for an approved purpose and covered by the publisher's field permissions.

## Record a check in Gate Monitor

In **Vehicle tracking**, search the registration and choose **Record official check** as an administrator. Open the service, complete the official lookup, and enter the registration and confirmed values. The VAHAN and PUCC data are recorded separately by field. [VAHAN lookup](https://vahan.parivahan.gov.in/nrservices/faces/user/login.xhtml) · [Parivahan PUCC lookup](https://puc.parivahan.gov.in/puc/views/PucCertificate.xhtml)

For a provider integration, map the permitted response fields to the documented request model for `POST /api/v1/vehicles/{plate}/registry-check`. Keep the document credentials on the server. Use only date and descriptive fields supplied by the service. The example below illustrates Gate Monitor's input contract with a synthetic registration.

```json
{
  "registration_number": "DEMOREG1",
  "service": "PARIVAHAN_PUC",
  "checked_at": "2026-11-19T10:00:00+05:30",
  "details": {
    "puc_certificate_number": "DEMO-PUC",
    "puc_valid_until": "2027-02-28"
  }
}
```

Once the publisher grants access, test one authorised vehicle in the sandbox first. Keep the API documentation and a redacted sample response for field mapping. Provide credentials through the deployment's secret configuration.
