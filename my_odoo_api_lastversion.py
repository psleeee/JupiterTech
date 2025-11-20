""" An API to query Odoo 
    Th. Estier - mars 2025

    You need an API that can publish the following operations:
    • Return a list of all business customers (who are companies).
    • Return a list of all orders and order lines placed by Agrolait (or any other
    customer).
    • Confirm a quote that has been sent to upgrade it to confirmed order status.
    Odoo ERP
    • Create or request the creation of the invoice corresponding to this order.
    • Cancel a quote.
    • Confirm (accept) or reject the scheduled delivery dates for an order, and if
    possible propose an alternative date.
    • Obtain a PDF version of an invoice or purchase order.
"""


from fastapi import FastAPI, Response
import xmlrpc.client
from typing import List
import base64
from urllib.parse import urljoin
from datetime import datetime
from typing import Optional
from pydantic import BaseModel

URL = 'https://edu-heclausanne-jupiter.odoo.com'
DB = 'edu-heclausanne-jupiter'
USER = 'oliviakusch@gmail.com'
PW = 'Blueflowers!1'
UID = False

class ContactFormRequest(BaseModel):
    name: str
    email: str
    issue_type: str
    message: str

def connect_odoo(hosturl, db, user, pw):
    "establish connection with odoo, return authentified uid "
    #connect to the common endpoint, which handles authentication
    common = xmlrpc.client.ServerProxy(f'{hosturl}/xmlrpc/2/common')
    #authenticate and get our userID
    uid = common.authenticate(db, user, pw, {})
    if uid:
        return (uid, common)
    else:
        raise ConnectionError("bad username or password")

class CustomerUpdateRequest(BaseModel):
    phone: str | None = None
    mobile: str | None = None
    email: str | None = None
    website: str | None = None

# initial setting
UID, c = connect_odoo(URL, DB, USER, PW)

app = FastAPI()

###
###  / g e t - s t a t u s
### 
'''
Users can see what version and status the Odoo server is currently running on.
'''
@app.get("/get-status", tags=["⚙️ System Status"])
async def hello():
    "get current odoo status and version"
    try:
        #check connection
        uid, common = connect_odoo(URL, DB, USER, PW)
        v = common.version()
        return {"Message" : f"user {uid} connected: Odoo version {v['server_version']} is waiting for requests on {DB}."}
    except Exception as err:
        return {"Message" : f"Problème de connexion au serveur {URL}, base:{DB}, utilisateur:{USER}",
                "Error" : f"Unexpected  {type(err)} : {err}"}

########################################################################
################### CUSTOMER & CUSTOMER DATA ###########################
########################################################################


###
###  / c u s t o m e r s / l i s t
###
'''
Return a list of all business customers
'''
@app.get("/customers/list", tags= ["👥Customers & Customer Data"])
async def get_all_customers():
    "get list of all customers, return dict of pairs (value:id, label:name)"
    models = xmlrpc.client.ServerProxy(f'{URL}/xmlrpc/2/object')
    #ajouter context = ssl._create_unverifitied_context()) si ne fonctionne pas
    #we want records that are a customer a company
    search_conditions = [('is_company','=', True), 
                         ('customer_rank','>=',0) ]   # > 0 means is already customer, >= 0 means is potentially customer
    #fields to return
    read_attributes = ['name', 'customer_rank']
    try:
        values = models.execute_kw(DB, UID, PW, 'res.partner', 'search_read', [search_conditions, read_attributes])
        #simpler format
        return [{'value':c['id'], 
                 'label':c['name']} for c in values]
    except Exception as err:
        return {"Message" : f"Odoo error:",
                "Error" : f"Unexpected  {type(err)} : {err}"}
###
###  / c u s t o m e r s / { c u s t _ i d }
###
@app.get("/customers/{cust_id}", tags= ["👥Customers & Customer Data"])
async def get_customer_data(cust_id: int):
    "get relevant data of a particular customer, return name, email, city, country"
    models = xmlrpc.client.ServerProxy(f'{URL}/xmlrpc/2/object')
    #fields that we want for the customer
    read_attributes = {'fields': ['name',
                                  'email',
                                  'city',
                                  'country_id', 
                                  'comment']}
    try:
        cust_info = models.execute_kw(DB, UID, PW, 'res.partner', 'read', [cust_id], read_attributes)
        return cust_info
    except Exception as err:
        return {"Message" : f"Odoo error:",
                "Error" : f"Unexpected  {type(err)} : {err}"}



########################################################################
########################## QUOTES ######################################
########################################################################

###
###  / q u o t e s / { c u s t _ i d }
###
@app.get("/quotes/{cust_id}", tags=["📋 Quotations"])
async def get_customer_quotes(cust_id: int):
    "retrieve all quotes (draft/sent orders) of a particular customer, return complete values"
    models = xmlrpc.client.ServerProxy(f'{URL}/xmlrpc/2/object')
    
    #search for orders in 'draft' or 'sent' state (quotes that haven't been confirmed yet)
    search_conditions = [('partner_id','=', cust_id), 
                         ('state', 'in', ['draft', 'sent'])]

    #fields to get from the sale.order model   
    quote_attributes = ['id',
                        'name', 
                        'state', 
                        'create_date', 
                        'amount_total',
                        'order_line']   # list of ids of order lines composing this quote
    try:
        quote_values = models.execute_kw(DB, UID, PW, 'sale.order', 'search_read', [search_conditions, quote_attributes])
        
        #get detailed order line information for each quote
        for quote in quote_values:
            solin_attributes = {'fields':['name',
                                          'product_id',
                                          'product_uom_qty', 
                                          'price_unit', 
                                          'price_total']}
            solin_values = models.execute_kw(DB, UID, PW, 'sale.order.line', 'read', [quote['order_line']], solin_attributes)
            quote['order_line'] = solin_values  # replace array of line ids by array of retrieved line values
            for line in solin_values:
                line['product_id'] = line['product_id'][0] # get the product id
        
        return quote_values
        
    except Exception as err:
        errmsg = str(err)
        return {"Message" : f"Odoo error:",
                "Error" : f"Unexpected  {type(err)} : {errmsg}"}

###
###  / q u o t e s / n e w _ o r d e r
###
@app.post("/quotes/new_order", tags=["📋 Quotations"])
async def create_sale_order(cust_id: int, product_ids: List[int]):
    "Create a quotation in draft state from a list of products selected by customer"
    models = xmlrpc.client.ServerProxy(f'{URL}/xmlrpc/2/object')
    partner_id = {'partner_id': cust_id}
    pid_fields = {'fields': ['name', 'list_price']}

    try:
        newso_id = models.execute_kw(DB, UID, PW, 'sale.order', 'create', [partner_id])
        for pid in product_ids:
            product = models.execute_kw(DB, UID, PW, 'product.product', 'read', [[pid]], pid_fields)
            product_info = product[0]

            psol_fields = {'order_id': newso_id, 'product_id': pid, 'name': product_info['name'], 'product_uom_qty': 1.0,
                           'price_unit': product_info['list_price']}
            psol = models.execute_kw(DB, UID, PW, 'sale.order.line', 'create', [psol_fields])

        return {
            "Message": f"Quotation {newso_id} created successfully.",
            "sale_order_id": newso_id
        }
    except Exception as err:
        errmsg = str(err)
        return {"Message": f"Odoo error:",
         "Error": f"Unexpected  {type(err)} : {errmsg}"}

########################################################################
########################## SALES ORDERS ################################
########################################################################

###
###  / s a l e o r d e r s / { c u s t _ i d }
###
@app.get("/saleorders/{cust_id}", tags=["💰 Sales Orders"])
async def get_customer_so(cust_id: int):
    "retrieve all sale orders of a particular customer, return complete values"
    models = xmlrpc.client.ServerProxy(f'{URL}/xmlrpc/2/object')
    #search orders that are confirmed 'sale'
    search_conditions = [('partner_id','=', cust_id), ('state', '=', 'sale')]  # show confirmed sales
    so_attributes = ['name', 
                     'state', 
                     'create_date', 
                     'amount_total',
                     'order_line']   # list of ids of order lines composing this so
    try:
        so_values = models.execute_kw(DB, UID, PW, 'sale.order', 'search_read', [search_conditions, so_attributes])
        for so in so_values:
            solin_attributes = {'fields':['name', 
                                          'product_uom_qty', 
                                          'price_unit', 
                                          'price_total']}
            solin_values = models.execute_kw(DB, UID, PW, 'sale.order.line', 'read', [so['order_line']], solin_attributes)
            so['order_line'] = solin_values  # replace array of line ids by array of retrieved line values
        return so_values
    except Exception as err:
        errmsg = str(err)
        return {"Message" : f"Odoo error:",
                "Error" : f"Unexpected  {type(err)} : {errmsg}"}

###
###  / s a l e o r d e r / c o n f i r m / { s o _ i d }
###
@app.post("/saleorders/confirm", tags=["💰 Sales Orders"])
async def confirm_sale_order(so_ids: List[int]):
    "Confirm one or more quotes by their IDs"
    models = xmlrpc.client.ServerProxy(f'{URL}/xmlrpc/2/object')
    try:
        #call action_confirm method 
        result = models.execute_kw(DB, UID, PW, 'sale.order', 'action_confirm', [so_ids])
        return {"Message" : f"Sale Order {so_ids} confirmed successfully.",
                "Result": result}
    except Exception as err:
        errmsg = str(err)
        return {"Message" : f"Odoo error confirming SO {so_ids}:",
                "Error" : f"Unexpected  {type(err)} : {errmsg}"}


###
###  / s a l e o r d e r / c a n c e l / { s o _ i d }
###
@app.post("/saleorder/send_email_and_cancel/{so_id}", tags=["💰 Sales Orders"])
async def send_and_cancel_sale_order(so_id: int):
    "Cancels an order and sends the cancellation email"
    models = xmlrpc.client.ServerProxy(f'{URL}/xmlrpc/2/object')


    try:
        #check if the order exists and get its state
        sale_order = models.execute_kw(DB, UID, PW, 'sale.order', 'search_read',
                                       [[('id', '=', so_id)]],
                                       {'fields': ['state']})
       
        if not sale_order:
            return {"Message": f"Sale order ID {so_id} not found."}

        #check if the order is already canceled
        current_state = sale_order[0]['state']
        if current_state == 'cancel':
            return {"Message": f"Sale order {so_id} is already in 'cancel' state."}
           
       
        #step 1: Create the wizard record.
        #this is like opening the pop-up and filling it with default values
        wizard_id = models.execute_kw(DB, UID, PW,
                                      'sale.order.cancel',    
                                      'create',              
                                      [{'order_id': so_id}])  


        #step 2: Call the 'Send and cancel' button's method on the wizard
        #this will send the email and then cancel, clicking on the button
        result = models.execute_kw(DB, UID, PW,
                                   'sale.order.cancel',      
                                   'action_send_mail_and_cancel',
                                   [[wizard_id]])            


        return {"Message" : f"Sale Order {so_id} cancelled and email sent.",
                "Result": result}
    except Exception as err:
        errmsg = str(err)
        return {"Message" : f"Odoo error sending and cancelling SO {so_id}:",
                "Error" : f"Unexpected  {type(err)} : {errmsg}"}

########################################################################
########################## PRODUCTS ####################################
########################################################################


###
###  / p r o d u c t s /
###

@app.get("/products/", tags=["🛒 Products"])
async def get_products():
    "get list of all products available"
    models = xmlrpc.client.ServerProxy(f'{URL}/xmlrpc/2/object')
    search_conditions = [('sale_ok', '=', True)]
    read_attributes = ['id', 'name','list_price', 'default_code']

    try:
        values = models.execute_kw(DB, UID, PW, 'product.product', 'search_read', [search_conditions, read_attributes])
        return [{'value':c['id'],
                 'label':c['name']} for c in values]
    except Exception as err:
        return {"Message": f"Odoo error:",
                "Error": f"Unexpected  {type(err)} : {err}"}

########################################################################
########################## INVOICES ####################################
########################################################################

###
###  / i n v o i c e s / { c u s t _ i d }
###


@app.get("/invoices/{cust_id}", tags=["🧾 Invoices"])
async def get_customer_invoices(cust_id: int):
    "retrieve all customer invoices of a particular customer, return complete values"
    models = xmlrpc.client.ServerProxy(f'{URL}/xmlrpc/2/object')
   
    #we search for the customer (partner_id) and ensure it's a Customer Invoice
    search_conditions = [('partner_id','=', cust_id),
                         ('move_type', '=', 'out_invoice')]
                         
    invoice_attributes = ['name',
                          'state',
                          'invoice_date',
                          'amount_total',
                          'invoice_line_ids']  
    try:
        #search invoices matching
        invoice_values = models.execute_kw(DB, UID, PW, 'account.move', 'search_read', [search_conditions, invoice_attributes])
       
        for inv in invoice_values:
            line_attributes = {'fields':['name',
                                         'quantity',
                                         'price_unit',
                                         'price_total']}
           
            line_values = models.execute_kw(DB, UID, PW, 'account.move.line', 'read', [inv['invoice_line_ids']], line_attributes)
           
            inv['invoice_line_ids'] = line_values  
           
        return invoice_values
    except Exception as err:
        errmsg = str(err)
        return {"Message" : f"Odoo err",
                "Error" : f"Unexpected  {type(err)} : {errmsg}"}

###
###  / i n v o i c e s / { i n v o i c e _ i d }
###
@app.get("/invoice_info/{invoice_id}", tags=["🧾 Invoices"])
async def get_invoice_info(invoice_id: int):
    """Retrieve detailed information for a specific invoice by its ID"""
    models = xmlrpc.client.ServerProxy(f'{URL}/xmlrpc/2/object')


    try:
        #define the fields you want to retrieve from the invoice
        invoice_fields = ['id', 'name', 'state', 'amount_total', 'invoice_date', 'partner_id', 'invoice_line_ids']


        #read the invoice record
        invoice = models.execute_kw(DB, UID, PW, 'account.move', 'read', [[invoice_id]], {'fields': invoice_fields})
        if not invoice:
            return {"Message": f"Invoice {invoice_id} not found"}


        invoice = invoice[0]  #extract the single record


        #fetch invoice line details
        if invoice['invoice_line_ids']:
            line_fields = ['name', 'product_id', 'quantity', 'price_unit', 'price_subtotal']
            invoice_lines = models.execute_kw(DB, UID, PW, 'account.move.line', 'read', [invoice['invoice_line_ids']], {'fields': line_fields})
        else:
            invoice_lines = []


        invoice['invoice_lines'] = invoice_lines
        del invoice['invoice_line_ids']  #remove raw IDs if not needed


        return invoice


    except Exception as err:
        errmsg = str(err)
        return {"Message": "Odoo error:", "Error": f"Unexpected {type(err)} : {errmsg}"}
###
###  / i n v o i c e s / { i n v o i c e _ i d } / p r e v i e w _ u r l
###
@app.get("/invoices/{invoice_id}/preview_url", tags=["🧾 Invoices"])
async def get_invoice_preview_url(invoice_id: int):
    """
    Returns the public URL for previewing an Invoice, combining the Odoo base URL 
    with the relative URL path containing the access token.
    """
    models = xmlrpc.client.ServerProxy(f'{URL}/xmlrpc/2/object')

    try:
        preview_data = models.execute_kw(
            DB, UID, PW,
            'account.move',
            'preview_invoice', 
            [[invoice_id]]
        )

        if preview_data and isinstance(preview_data, dict) and 'url' in preview_data:
            # Combine the base Odoo URL with the relative path provided by Odoo
            full_preview_url = urljoin(URL, preview_data['url'])
            
            return {
                "invoice_id": invoice_id, 
                "full_preview_url": full_preview_url,
                "raw_odoo_response": preview_data # Keep the raw data for debugging/completeness
            }
        else:
            return {"Message": f"Could not generate preview URL for Invoice ID {invoice_id}. "
                               f"Ensure the invoice is posted and public access is configured."}

    except Exception as err:
        errmsg = str(err)
        return {"Message": f"Odoo error getting preview URL for Invoice {invoice_id}:",
                "Error": f"Unexpected {type(err)} : {errmsg}"}


########################################################################
########################## DELIVERIES ##################################
########################################################################

###
###  c u s t o m e r s  / { c u s t _ i d } / d e l i v e r i e s
###
@app.get("/customers/{cust_id}/deliveries", tags=["🚚 Delivery"])
async def get_customer_deliveries(cust_id: int):
    """
    Delivery overview for a customer:
    - All sale orders for this customer (state: 'sale' or 'done')
    - For each order: list of related pickings (deliveries) and a simple delivery_status
    """
    models = xmlrpc.client.ServerProxy(f'{URL}/xmlrpc/2/object')

    try:
        # 1) Get sale orders for this customer that are active / relevant
        so_search_conditions = [
            ('partner_id', '=', cust_id),
            ('state', 'in', ['sale', 'done'])  # confirmed or completed orders
        ]

        so_fields = ['id', 'name', 'state', 'date_order', 'picking_ids']

        sale_orders = models.execute_kw(
            DB, UID, PW,
            'sale.order', 'search_read',
            [so_search_conditions, so_fields]
        )

        if not sale_orders:
            return {
                "customer_id": cust_id,
                "orders": [],
                "Message": "No sale orders found for this customer."
            }

        result_orders = []

        for so in sale_orders:
            picking_ids = so.get('picking_ids', [])
            deliveries = []
            delivery_status = "no_delivery"

            if picking_ids:
                # 🔹 Only use fields that actually exist on stock.picking
                picking_fields = [
                    'id',
                    'name',
                    'state',
                    'scheduled_date',  # if this errors too, we can swap it for 'date_deadline'
                    'date_done',
                ]

                pickings = models.execute_kw(
                    DB, UID, PW,
                    'stock.picking', 'read',
                    [picking_ids],
                    {'fields': picking_fields}
                )

                deliveries = [
                    {
                        "picking_id": p['id'],
                        "name": p.get('name'),
                        "state": p.get('state'),
                        "scheduled_date": p.get('scheduled_date'),
                        "date_done": p.get('date_done'),
                    }
                    for p in pickings
                ]

                # 3) Compute a simple delivery_status based on picking states
                states = [p.get('state') for p in pickings]

                if states and all(s == 'done' for s in states):
                    delivery_status = "delivered"
                elif any(s == 'done' for s in states) and any(
                    s not in ['done', 'cancel'] for s in states
                ):
                    delivery_status = "partially_delivered"
                elif any(s in ['waiting', 'confirmed', 'assigned'] for s in states):
                    delivery_status = "pending_shipment"
                else:
                    delivery_status = "unknown"

            result_orders.append({
                "order_id": so['id'],
                "order_name": so.get('name'),
                "order_state": so.get('state'),
                "date_order": so.get('date_order'),
                "delivery_status": delivery_status,
                "deliveries": deliveries
            })

        return {
            "customer_id": cust_id,
            "orders": result_orders
        }

    except Exception as err:
        errmsg = str(err)
        return {
            "Message": "Odoo error while fetching deliveries for this customer",
            "Error": f"Unexpected {type(err)} : {errmsg}"
        }

###
###  / d e l i v e r y / { s o _ i d }
###
@app.put("/saleorders/validate_delivery/{so_id}", tags=["🚚 Delivery"])
async def validate_sale_order_delivery(so_id: int):
   
    models = xmlrpc.client.ServerProxy(f'{URL}/xmlrpc/2/object')


    try:
        #get sale order and its linked delivery ID
        sale_order = models.execute_kw(DB, UID, PW, 'sale.order', 'search_read',
                                       [[('id', '=', so_id)]],
                                       {'fields': ['state', 'picking_ids']})
        if not sale_order:
            return {"Message": f"Sale order ID {so_id} not found."}

      
        state = sale_order[0]['state']
        picking_ids = sale_order[0]['picking_ids']
        validated_pickings = []

        #if the order is still a draft, confirm it 
        if state == 'draft':
            models.execute_kw(DB, UID, PW, 'sale.order', 'action_confirm', [[so_id]])
            state = 'confirmed'


        # validate delivery pickings if exist
        if picking_ids:
            #get picking states
            pickings = models.execute_kw(DB, UID, PW, 'stock.picking', 'read',
                                         [picking_ids], {'fields': ['state']})
            for picking in pickings:
                if picking['state'] not in ['done', 'cancel']:
                    models.execute_kw(DB, UID, PW, 'stock.picking', 'button_validate', [[picking['id']]])
                    validated_pickings.append(picking['id'])


            #if any pickings validated, mark order as done
            if validated_pickings:
                state = 'done'


        return {
            "Message": f"Sale order processed. Current state: {state}",
            "ValidatedPickings": validated_pickings
        }


    except Exception as err:
        errmsg = str(err)
        return {"Message": "Odoo error",
                "Error": f"Unexpected {type(err)} : {errmsg}"}
    


    ###
###  / c u s t o m e r _ s e r v i c e / c o n t a c t
###
@app.post("/customer_service/contact", tags=["🎧 Customer Service"])
async def submit_contact_form(form_data: ContactFormRequest):
    """
    Receives contact form data and posts it as a raw list to Odoo 'Discuss'.
    """
    models = xmlrpc.client.ServerProxy(f'{URL}/xmlrpc/2/object')

    try:
        #we find the channel 
        channel_search = models.execute_kw(DB, UID, PW, 'discuss.channel', 'search', 
                                           [[('name', 'ilike', 'General')]])
        
        if not channel_search:
            channel_search = models.execute_kw(DB, UID, PW, 'discuss.channel', 'search', 
                                           [[('name', 'ilike', 'Support')]])
            
        if not channel_search:
             return {"Message": "Could not find a discussion channel to post to."}

        channel_id = channel_search[0]

        #format the message
        raw_list = [
            form_data.name,
            form_data.email,
            form_data.issue_type,
            form_data.message
        ]
        
        #list to string
        final_message = str(raw_list)

        #post the message
        models.execute_kw(DB, UID, PW, 'discuss.channel', 'message_post', 
                          [channel_id], 
                          {
                              'body': final_message, 
                              'message_type': 'comment',
                              'subtype_xmlid': 'mail.mt_comment',
                              'author_id': UID
                          })

        return {
            "Message": "Inquiry sent successfully.",
            "Posted_Data": final_message
        }

    except Exception as err:
        errmsg = str(err)
        return {"Message": "Odoo error posting to Discuss:",
                "Error": f"Unexpected {type(err)} : {errmsg}"}

# /cutomers/update_by_customer/{name}
@app.post("/customers/update_by_customer/{name}", tags= ["👥Customers & Customer Data"])
def update_partner_by_customer(name: str, data: CustomerUpdateRequest):
    """
    Update customer contact information by his or her name.
    """
    try:
        models = xmlrpc.client.ServerProxy(f'{URL}/xmlrpc/2/object')
        partner_ids = models.execute_kw(
            DB, UID, PW,
            "res.partner", "search",
            [[["name", "=", name]]]
        )
        print(name)

        if not name:
            return {"Message": "Could not find a customer."}

        partner_id = partner_ids[0]

        updates = {}
        if data.phone is not None:
            updates["phone"] = data.phone
        if data.mobile is not None:
            updates["mobile"] = data.mobile
        if data.email is not None:
            updates["email"] = data.email
        if data.website is not None:
            updates["website"] = data.website

        result = models.execute_kw(
            DB, UID, PW,
            "res.partner", "write",
            [[partner_id], updates]
        )

        return {
            "updated": result,
            "fields_changed": updates
        }
    except Exception as e:
        return {"Message": "status code 500."}
        
# 
# for starting api in terminal
# python -m uvicorn my_odoo_api_lastversion:app --reload --host 127.0.0.1 --port 8000



########################################################################
########################  FAILED FUNCTIONS   ###########################
########################################################################
'''
###
###  / i n v o i c e s / p a y / { i n v o i c e _ i d }
###
@app.post("/invoices/pay/{invoice_id}", tags=["🧾 Invoices"])
async def register_invoice_payment(invoice_id: int):
    """
    Registers a full payment for a specific Invoice ID by correctly passing the 
    context to the account.payment.register wizard and using the confirmed Bank Journal ID.
    """
    models = xmlrpc.client.ServerProxy(f'{URL}/xmlrpc/2/object')
    
    # --- CONFIRMED BANK JOURNAL ID ---
    BANK_JOURNAL_ID = 14  # <-- CONFIRMED from Metadata screenshot
    
    try:
        # 1. Get the invoice details: amount, partner ID, currency
        invoice_fields = ['name', 'amount_residual', 'currency_id', 'status_in_payment', 'partner_id']
        invoice_info = models.execute_kw(
            DB, UID, PW, 
            'account.move', 
            'read', 
            [[invoice_id]], 
            {'fields': invoice_fields}
        )
        
        if not invoice_info:
            return {"Message": f"Invoice ID {invoice_id} not found."}
        
        invoice = invoice_info[0]
        amount_to_pay = invoice['amount_residual']
        current_status = invoice['status_in_payment']
        partner_id = invoice['partner_id'][0] 
        currency_id = invoice['currency_id'][0] 

        # Check if payment is needed
        if amount_to_pay <= 0.01 or current_status == 'paid':
            return {"Message": f"Invoice {invoice['name']} is already paid or has a zero balance (Current Status: {current_status})."}
        
        # Define the context (tells Odoo this action is originating from an invoice)
        context = {
            'active_model': 'account.move',
            'active_ids': [invoice_id],
            'active_id': invoice_id
        }

        # 2. Directly create the account.payment.register wizard record.
        # Data record contains minimum required fields + the essential journal_id.
        wizard_id = models.execute_kw(
            DB, UID, PW, 
            'account.payment.register', 
            'create', 
            [{
                'amount': amount_to_pay,
                'payment_type': 'inbound',
                'partner_type': 'customer',
                'partner_id': partner_id,
                'journal_id': BANK_JOURNAL_ID, # <-- NOW USING CONFIRMED ID 14
                'currency_id': currency_id,
            }], 
            # Pass the context dictionary explicitly
            context 
        )
        
        if not wizard_id:
             raise Exception("Failed to create payment wizard record.")
        
        # 3. Execute the 'action_create_payments' method on the wizard record to finalize payment.
        models.execute_kw(
            DB, UID, PW, 
            'account.payment.register', 
            'action_create_payments', 
            [[wizard_id]],
            context
        )
        
        # 4. Read the final status to confirm payment
        final_status = models.execute_kw(
            DB, UID, PW, 
            'account.move', 
            'read', 
            [[invoice_id]], 
            {'fields': ['status_in_payment']}
        )[0]['status_in_payment']

        return {
            "Message": f"Payment of {amount_to_pay} registered successfully for {invoice['name']} via Bank Journal ID 14.",
            "Invoice_ID": invoice_id,
            "Initial_Status": current_status,
            "Final_Status": final_status 
        }

    except Exception as err:
        errmsg = str(err)
        return {"Message": f"Odoo error registering payment for Invoice {invoice_id}. Manual review may be required.",
                "Error": f"Unexpected {type(err)} : {errmsg}"}
'''
