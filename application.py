from flask import Flask, render_template, redirect, url_for, session, request
from warrant import Cognito
from config import cognito_userpool_id, cognito_app_client_id
from models.bert.inference_bert import Inference
import wikipedia
from rake_nltk import Rake
from database.Database import Database
from datetime import datetime
from wikipedia.exceptions import PageError
# from botocore.errorfactory
inference = Inference()

app = Flask(__name__)
database = Database()
KEYWORD_TOP_K = 5
MIN_ANSWER_SCORE = 0
keyword_topk = 5

@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        if valid_login(request.form['username'], request.form['password']):
            session['username'] = request.form.get('username')
            return redirect(url_for('welcome'))
        else:
            error = 'Incorrect username and password'
            app.logger.warning('Incorrect Username and password for user (%s)', request.form.get('username'))
    return render_template('login.html', error=error)

@app.route('/logout')
def logout():
    session.pop('username', None)
    return redirect(url_for('login'))

@app.route('/signup', methods=['GET', "POST"])
def signup():
    error = None
    if request.method == 'POST':
        if len(request.form['password']) < 8:
            error = 'Password too short!'
        else:
            cognito = Cognito(user_pool_id=cognito_userpool_id, client_id=cognito_app_client_id)
            cognito.add_base_attributes(email=request.form['email'])
            try:
                cognito.register(username=request.form['username'], password=request.form['password'])
                _ = database.add_user(request.form['username'],request.form['password'],request.form['email'])
            except Exception as e:
                
                if e.response['Error']['Code'] == 'UsernameExistsException':
                    error = 'User Already Exists!'
                else:
                    error = 'Unexpected Error: {}'.format(e.response['Error']['Message'])
                return render_template('signup.html', error=error)
            session['username'] = request.form['username']
            return redirect(url_for('verification'))
    return render_template('signup.html', error=error)

@app.route('/verification', methods=['GET', 'POST'])
def verification():
    if 'username' in session:
        username = session['username']
        if request.method == 'POST':
            cognito = Cognito(user_pool_id=cognito_userpool_id, client_id=cognito_app_client_id, username=username)
            try:
                cognito.confirm_sign_up(confirmation_code=request.form['vcode'])
                return redirect(url_for('welcome'))
            except Exception as e:
                print(e)
                error = 'Unable to verify, please try again'
                return render_template('signup.html', error=error)

        return render_template("verification.html")
    else:
        return redirect(url_for('login'))

@app.route('/', methods=['GET', 'POST'])
def welcome():
    if 'username' in session:
        return render_template('welcome.html', username=session['username'])
    else:
        return redirect(url_for('login'))

@app.route('/with_context', methods=['GET', 'POST'])
def with_context():
    if 'username' in session:
        if request.method == 'POST':
            
            question = request.form['question']
            if not question.endswith('?'):
                question += '?'
            
            context = request.form['passage']
            qas = {
                'question': question,
                'context_list': [context]
            }
            
            results = inference.response(qas=qas)
            if not results:
                return redirect(url_for('result_no_answer'))
            
            answer, score = results[0]
            
            if not answer or score < MIN_ANSWER_SCORE:
                return redirect(url_for('result_no_answer'))
            
            user_id = database.get_id_by_name(session['username'])
            database.update(user_id, str(datetime.now()), request.form['passage'], request.form['question'],answer)
            
            return redirect(url_for('result', question=question, answer=answer))
        return render_template('with_context.html', username=session['username'])
    else:
        return redirect(url_for('login'))

@app.route('/without_context', methods=['GET', 'POST'])
def without_context():
    if 'username' in session:
        if request.method == 'POST':
            
            question = request.form['question']
            if not question.endswith('?'):
                question += '?'
            
            rake = Rake()
            rake.extract_keywords_from_text(question)
            keywords = rake.get_ranked_phrases()[:5]

            context_list = []
            for keyword in keywords:
                try:
                    context = wikipedia.page(keyword).summary
                except PageError as e:
                    print(e)
                    continue
                context_list += get_context_list(context=context)
            
            if not context_list:
                return redirect(url_for('result_no_answer'))
            
            qas = {
                'question': question,
                'context_list': context_list
            }
            
            results = inference.response(qas=qas)
            
            final_answer = ""
            max_score = float("-inf")
            final_context = ""

            for i, result in enumerate(results):
                answer, score = result
                
                if score > max_score and answer:
                    final_answer = answer
                    max_score = score
                    final_context = context_list[i // 3]
            
            if not final_answer or max_score < MIN_ANSWER_SCORE:
                return redirect(url_for('result_no_answer'))

            user_id = database.get_id_by_name(session['username'])
            database.update(user_id, str(datetime.now()), final_context, request.form['question'], final_answer)
            
            return redirect(url_for('result', question=request.form['question'], answer=final_answer))
        else:
            return render_template('without_context.html', username=session['username'])
    else:
        return redirect(url_for('login'))

@app.route('/result/<question>/<answer>', methods=['GET', 'POST'])
def result(question="", answer=""):
    if 'username' in session:
        return render_template('result_with_context.html', username=session['username'], question=question, answer=answer)
    else:
        return redirect(url_for('login'))
    
@app.route('/result_no_answer/', methods=['GET', 'POST'])
def result_no_answer():
    if 'username' in session:
        return render_template('result_no_answer.html', username=session['username'])
    else:
        return redirect(url_for('login'))

@app.route('/history', methods=['GET'])
def history():
    if 'username' in session:
        requested_history = database.get_history_list(name=session['username'], limit=5)
        if requested_history == -1:
            requested_history = [("This will not be shown", "You do not have any question history now", "Go to ask Nucleus something!")]
        return render_template('history.html', username=session['username'], requested_history=requested_history)
    else:
        return redirect(url_for('login'))
    
@app.route('/history/<cnt>', methods=['GET'])
def history_count(cnt=5):
    if 'username' in session:
        requested_history = database.get_history_list(name=session['username'], limit=cnt)
        if requested_history == -1:
            requested_history = [("This will not be shown", "You do not have any question history now", "Go to ask Nucleus something!")]
        return render_template('history_count.html', username=session['username'], requested_history=requested_history)
    else:
        return redirect(url_for('login'))
    
@app.route('/history_redirect', methods=['GET', 'POST'])
def history_redirect():
    if 'username' not in session:
        return redirect(url_for('login'))
    if request.method == 'POST':
        return redirect(url_for('history_count', cnt=request.form['cnt']))
    return render_template('history_redirect.html', username=session['username'])
    
@app.route('/feedback/<question>/<answer>', methods=['GET', 'POST'])
def feedback(question=None, answer=None):
    if 'username' not in session:
        return redirect(url_for('login'))
    if request.method == 'POST':
        database.user_feedback(session['username'], question, answer, int(request.form['score']),
                               request.form['expected_answer'])
        return redirect(url_for("thankyou", username=session['username']))
    else:
        return render_template('feedback.html', username=session['username'], question=question, answer=answer)
    
@app.route('/thankyou', methods=['GET'])
def thankyou():
    if 'username' not in session:
        return redirect(url_for('login'))
    return render_template('thankyou.html', username=session['username'])

def valid_login(username, password):
    cognito = Cognito(cognito_userpool_id, cognito_app_client_id, username=username)
    try:
        cognito.authenticate(password)
    except Exception as e:
        print(e)
        return False
    return True

def get_context_list(context, min_len=700):
    context_list = []
    assert context
    p1, p2 = 0, 0
    while p2 < len(context):
        p2 += min_len
        while p2 < len(context) and context[p2] != '.':
            p2 += 1
        p2 += 1
        context_list.append(context[p1:p2])
        p1 = p2
    return context_list

if __name__ == '__main__':
    app.debug = True
    app.secret_key = '\xe3-\xe1\xf7\xfb\x91\xb1\x8c\xae\xf2\xc1BH\xe0/K~~%>ac\t\x01'
    app.run()
    