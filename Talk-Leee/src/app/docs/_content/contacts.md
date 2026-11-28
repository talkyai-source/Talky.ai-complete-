The Contacts area is used to manage people who may be called by campaigns.

## Open Contacts

1. Select **Contacts**.
2. Choose the relevant campaign or contact list when required.
3. Use search to locate a contact.

![The contacts list, showing three contacts with their companies and numbers](/docs/contacts-01-list.webp)

## Upload Contacts by CSV

1. Open **Contacts**.
2. Select the campaign/contact list.
3. Choose **Upload CSV** or **Add more contacts**.

   ![The CSV upload panel, with a link to download a template](/docs/contacts-02-csv-upload.webp)
4. If needed, select **Download a sample file** to get a CSV with the right columns and two example rows.
5. Choose the CSV file.
6. Review the import preview and any validation errors.
7. Complete **Permission to call these contacts** (see below).
8. Select **Upload Contacts**.
9. Review the import results.

   ![An import summary, listing the rows imported and the rows that could not be](/docs/contacts-03-import-result.webp)

Talk-Lee reports successfully imported rows and rows that could not be imported.

Contact uploads are unlimited on all plans.

## Add one contact

1. Open **Contacts** and choose the campaign.
2. Select **Add contact**.
3. Enter the contact's details.
4. Complete **Permission to call these contacts** (see below).
5. Save the contact.

## Permission to call these contacts

Every way of adding contacts asks for this block: the CSV upload, **Add contact** and the Salesforce import. We can only call people you are allowed to contact, so you say where that permission comes from, and it is kept with the list.

- Choose why you can call them.
- Describe where the permission comes from (at least 5 characters).
- Choose the date permission was given. If it varies, use the most recent date. It cannot be in the future.
- Tick the box that confirms you are allowed to call the people in the list and can show a record of this if asked.

## Phone number format

Store numbers in full international format, including the country code, so they can be dialled reliably from any market. Talk-Lee normalises what it can, but a number missing its country code may fail to connect.

## Edit or Delete Contacts

Use the available edit action to correct contact information. Use delete only when the contact should be removed.

Always respect opt-out/do-not-call requirements in your operating process.

## Opt-outs and do-not-call

When a person asks not to be contacted again, record that outcome so campaigns do not call them. Review your own compliance obligations for the markets you call into.

The **Do-Not-Call list** holds numbers no campaign will call. Callers who ask to stop are added automatically. Open it with the **Do-Not-Call list** link above the Contacts list.

- To add one number, use **Add a number**: enter the phone number, choose its country, add a reason if you wish, then select **Add to list**.
- To paste many numbers, use **Import many**: paste the numbers into **Numbers, one per line**, choose the country, then select **Import numbers**. The result says how many were added, how many were already listed and how many were not valid.
- To find a number on the list, search under **Numbers on the list**. Each entry has a **Remove** control.

Only a role that may change the list sees **Add a number** and **Import many**. Other roles see the list without those two forms.

## Import from Salesforce

Once Salesforce is connected under **Connectors**, its settings offer **Import Leads or Contacts into a campaign**. Choose Lead or Contact and the campaign, set the maximum number of records (up to 2,000), and add a filter or a list name if you wish. Complete **Permission to call these contacts**, then start the import.

The import pulls records that have a phone number (unconverted Leads only), newest first, into a new contact list. Each imported contact remembers its Salesforce Id. Salesforce is Limited, so its card appears under **Connectors** only once it is switched on for the service; see the Connectors page.
