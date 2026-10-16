# WebWatch Quick Add

This Manifest V3 Chromium extension opens the local WebWatch dashboard to manage existing pages or sends the active tab's public HTTP(S) URL to the dashboard for review. It does not contact WebWatch's API, submit a website automatically, or read page contents. The user must click the extension and then submit WebWatch's Add website form to add a page.

## Install for local use

1. Start WebWatch at `http://127.0.0.1:5000`.
2. In Chrome or Edge, open the browser's extensions page and enable Developer mode.
3. Choose **Load unpacked** and select this `browser-extension` directory.
4. Open a public webpage, click the WebWatch Quick Add toolbar button, then choose **Review in WebWatch**.
5. Review the prefilled URL and submit **Add website** in WebWatch.

Choose **Manage monitored pages** to open the dashboard without reading the active tab URL.

The extension requests only `activeTab`. It uses that temporary, user-initiated access to read the active tab URL, and opens WebWatch with the URL in the fragment so it is not sent in the HTTP request line. The dashboard consumes and clears the fragment before the user submits anything. WebWatch still performs its normal public-address, credential, and URL validation.
