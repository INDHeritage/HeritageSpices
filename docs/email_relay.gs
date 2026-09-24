/**
 * Heritage Spices - free e-mail relay (Google Apps Script)
 *
 * Why: Render's free plan blocks the ports used to send e-mail directly. This tiny script runs
 * inside your Google account and sends the mail from your own Gmail address when the website
 * asks it to over normal HTTPS (which Render allows).
 *
 * Setup (5 minutes, once):
 *  1. Go to https://script.google.com  ->  New project.  Delete the sample code, paste ALL of this.
 *  2. Replace PASTE_A_LONG_RANDOM_SECRET_HERE below with a long random string (20+ letters/digits).
 *     Keep a copy: the SAME value goes into Render as EMAIL_WEBHOOK_SECRET.
 *  3. Click Deploy -> New deployment -> gear icon -> "Web app".
 *        Execute as:      Me
 *        Who has access:  Anyone
 *     Click Deploy, then Authorize access (choose your Gmail; if Google says "unverified app",
 *     click Advanced -> Go to project). This lets the script send mail as you.
 *  4. Copy the "Web app URL" (ends with /exec). It goes into Render as EMAIL_WEBHOOK_URL.
 *
 * Limits: a normal Gmail account can send about 100 e-mails a day this way - plenty for a
 * shop that ships a few orders a day (each order sends up to 3 e-mails).
 * If you ever edit the script, use Deploy -> Manage deployments -> Edit -> New version.
 */
const SECRET = 'PASTE_A_LONG_RANDOM_SECRET_HERE';

function doPost(e) {
  try {
    const data = JSON.parse(e.postData.contents);
    if (!SECRET || data.secret !== SECRET) {
      return reply({ ok: false, error: 'unauthorized' });
    }
    if (!data.to || !data.subject) {
      return reply({ ok: false, error: 'missing recipient or subject' });
    }
    MailApp.sendEmail({
      to: data.to,
      subject: data.subject,
      body: data.text || '',
      htmlBody: data.html || undefined,
      name: data.name || 'Heritage Spices'
    });
    return reply({ ok: true });
  } catch (err) {
    return reply({ ok: false, error: String(err) });
  }
}

function reply(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}
