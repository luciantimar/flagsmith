import data from 'common/data/base/_data'

const ForgotPassword = class extends React.Component {
  constructor(props, context) {
    super(props, context)
    this.state = {
      email: props.initialValue,
    }
  }

  componentDidMount() {
    setTimeout(() => {
      const emailField = document.querySelector(
        'input[name="forgotPasswordEmail"]',
      )
      if (emailField) {
        emailField.focus()
      }
    }, 1000)
  }

  handleSubmit = (e) => {
    e.preventDefault()
    const { email } = this.state
    if (Utils.isValidEmail(email)) {
      // data.post(`${Project.api}auth/password/reset/`, { email })
      data
        .post(`${Project.api}auth/users/reset_password/`, { email })
        .then(() => {
          this.props.onComplete && this.props.onComplete()
          closeModal()
        })
        .catch((error) => {
          this.setState({ error })
        })
    }
  }

  render() {
    return (
      <div>
        <p>Please enter your email address</p>
        <form onSubmit={this.handleSubmit}>
          <InputGroup
            inputProps={{
              className: 'full-width mb-2',
              name: 'forgotPasswordEmail',
            }}
            title='Email Address'
            placeholder='email'
            type='email'
            value={this.state.email}
            onChange={(e) =>
              this.setState({ email: Utils.safeParseEventValue(e) })
            }
          />

          {this.state.error && (
            <div className='alert alert-danger'>{this.state.error}</div>
          )}
          <Button
            disabled={!Utils.isValidEmail(this.state.email)}
            onClick={this.handleSubmit}
          >
            Send
          </Button>
        </form>
      </div>
    )
  }
}
module.exports = ForgotPassword
