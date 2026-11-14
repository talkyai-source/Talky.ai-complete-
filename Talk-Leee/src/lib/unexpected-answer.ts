/**
 * The app's own "this answer is not valid" error. It carries the same code as
 * the API client's response-validation failure (`invalid_response`), so a
 * failed read shows the ErrorState default instead of this technical sentence.
 * The message is kept for logs.
 */
export class UnexpectedAnswerError extends Error {
    readonly code = "invalid_response";

    constructor(message: string) {
        super(message);
        this.name = "UnexpectedAnswerError";
    }
}
