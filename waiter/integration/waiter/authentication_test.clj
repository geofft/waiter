(ns waiter.authentication-test
  (:require [clj-time.core :as t]
            [clojure.data.json :as json]
            [clojure.string :as str]
            [clojure.test :refer :all]
            [clojure.tools.logging :as log]
            [clojure.walk :as walk]
            [waiter.status-codes :refer :all]
            [waiter.util.client-tools :refer :all]
            [waiter.util.utils :as utils])
  (:import (java.net URI)))

(deftest ^:parallel ^:integration-fast test-default-composite-authenticator
  (testing-using-waiter-url
    (when (using-composite-authenticator? waiter-url)
      (let [token (rand-name)
            response (post-token waiter-url (dissoc (assoc (kitchen-params)
                                                      :name token
                                                      :permitted-user "*"
                                                      :run-as-user (retrieve-username)
                                                      :token token)
                                                    :authentication))]
        (try
          (assert-response-status response http-200-ok)
          (let [{:keys [service-id body]} (make-request-with-debug-info
                                            {:x-waiter-token token}
                                            #(make-kitchen-request waiter-url % :path "/request-info"))
                body-json (json/read-str (str body))]
            (with-service-cleanup
              service-id
              (is (= (retrieve-username) (get-in body-json ["headers" "x-waiter-auth-principal"])))))
          (finally
            (delete-token-and-assert waiter-url token)))))))

(deftest ^:parallel ^:integration-fast test-token-authentication-parameter-error
  (testing-using-waiter-url
    (when (using-composite-authenticator? waiter-url)
      (let [authentication-providers (-> waiter-url
                                       waiter-settings
                                       (get-in [:authenticator-config :composite :authentication-providers])
                                       keys
                                       (->> (map name)))
            error-message (str "authentication must be one of: '"
                               (str/join "', '" (sort (into #{"disabled" "standard"} authentication-providers)))
                               "'")]
        (let [token (rand-name)
              {:keys [body] :as response} (post-token waiter-url (assoc (kitchen-params)
                                                                   :authentication "invalid"
                                                                   :name token
                                                                   :permitted-user "*"
                                                                   :run-as-user (retrieve-username)
                                                                   :token token))]
          (assert-response-status response http-400-bad-request)
          (is (str/includes? body error-message)))
        (let [token (rand-name)
              {:keys [body] :as response} (post-token waiter-url (assoc (kitchen-params)
                                                                   :authentication ""
                                                                   :name token
                                                                   :permitted-user "*"
                                                                   :run-as-user (retrieve-username)
                                                                   :token token))]
          (assert-response-status response http-400-bad-request)
          (is (str/includes? body error-message)))))))

(defn- retrieve-access-token
  [realm]
  (if-let [access-token-url-env (System/getenv "WAITER_TEST_JWT_ACCESS_TOKEN_URL")]
    (let [access-token-url (str/replace access-token-url-env "{HOST}" realm)
          access-token-uri (URI. access-token-url)
          protocol (.getScheme access-token-uri)
          authority (.getAuthority access-token-uri)
          path (str (.getPath access-token-uri) "?" (.getQuery access-token-uri))
          access-token-response (make-request authority path :headers {"x-iam" "waiter"} :protocol protocol)
          _ (assert-response-status access-token-response http-200-ok)
          access-token-response-json (-> access-token-response :body str json/read-str)
          access-token (get access-token-response-json "access_token")]
      (log/info "retrieved access token" {:access-token access-token :realm realm})
      access-token)
    (throw (ex-info "WAITER_TEST_JWT_ACCESS_TOKEN_URL environment variable has not been provided" {}))))

(defmacro assert-auth-cookie
  "Helper macro to assert the value of the set-cookie header."
  [set-cookie assertion-message]
  `(let [set-cookie# ~set-cookie
         assertion-message# ~assertion-message]
     (is (str/includes? set-cookie# "auth-expires-at=") assertion-message#)
     (is (str/includes? set-cookie# "x-waiter-auth=") assertion-message#)
     (is (str/includes? set-cookie# "Max-Age=") assertion-message#)
     (is (str/includes? set-cookie# "Path=/") assertion-message#)
     (is (str/includes? set-cookie# "HttpOnly=true") assertion-message#)))

;; Test disabled because JWT support is, currently, only for tokens
(deftest ^:parallel ^:integration-fast ^:explicit test-successful-jwt-authentication-waiter-realm
  (testing-using-waiter-url
    (if (jwt-auth-enabled? waiter-url)
      (let [waiter-host (-> waiter-url sanitize-waiter-url utils/authority->host)
            access-token (retrieve-access-token waiter-host)
            request-headers {"authorization" (str "Bearer " access-token)
                             "host" waiter-host
                             "x-forwarded-proto" "https"}
            port (waiter-settings-port waiter-url)
            target-url (str waiter-host ":" port)
            {:keys [body headers] :as response}
            (make-request target-url "/waiter-auth" :disable-auth true :headers request-headers :method :get)
            set-cookie (str (get headers "set-cookie"))
            assertion-message (str {:headers headers
                                    :set-cookie set-cookie
                                    :target-url target-url})]
        (assert-response-status response http-200-ok)
        (is (= (retrieve-username) (str body)))
        (is (= "jwt" (get headers "x-waiter-auth-method")) assertion-message)
        (is (= (retrieve-username) (get headers "x-waiter-auth-user")) assertion-message)
        (assert-auth-cookie set-cookie assertion-message))
      (log/info "JWT authentication is disabled"))))

;; Test disabled because JWT support is, currently, only for tokens
(deftest ^:parallel ^:integration-fast ^:explicit test-forbidden-jwt-authentication-waiter-realm
  (testing-using-waiter-url
    (if (jwt-auth-enabled? waiter-url)
      (let [waiter-host (-> waiter-url sanitize-waiter-url utils/authority->host)
            access-token (retrieve-access-token waiter-host)
            request-headers {"authorization" (str "Bearer " access-token)
                             "host" waiter-host
                             "x-forwarded-proto" "http"}
            port (waiter-settings-port waiter-url)
            target-url (str waiter-host ":" port)
            {:keys [body headers] :as response}
            (make-request target-url "/waiter-auth" :disable-auth true :headers request-headers :method :get)
            set-cookie (str (get headers "set-cookie"))
            assertion-message (str {:headers headers
                                    :target-url target-url})]
        (assert-response-status response http-403-forbidden)
        (is (str/includes? (str body) "Must use HTTPS connection") assertion-message)
        (is (str/blank? set-cookie) assertion-message))
      (log/info "JWT authentication is disabled"))))

(deftest ^:parallel ^:integration-fast test-forbidden-authentication-with-bad-jwt-token-waiter-realm
  (testing-using-waiter-url
    (if (jwt-auth-enabled? waiter-url)
      (let [waiter-host (-> waiter-url sanitize-waiter-url utils/authority->host)
            port (waiter-settings-port waiter-url)
            target-url (str waiter-host ":" port)]
        (let [access-token (str (retrieve-access-token waiter-host) "invalid")
              request-headers {"authorization" [(str "Bearer " access-token)
                                                (str "Negotiate bad-token")
                                                (str "SingleUser forbidden")]
                               "host" waiter-host
                               "x-forwarded-proto" "https"}
              {:keys [headers] :as response}
              (make-request target-url "/waiter-auth" :disable-auth true :headers request-headers :method :get)
              set-cookie (str (get headers "set-cookie"))
              assertion-message (str (select-keys response [:body :error :headers :status]))]
          (assert-response-status response http-403-forbidden)
          (is (str/blank? (get headers "www-authenticate")) assertion-message)
          (is (str/blank? set-cookie) assertion-message))

        (when use-spnego
          (let [{:keys [allow-bearer-auth-api? attach-www-authenticate-on-missing-bearer-token?]
                 :or {allow-bearer-auth-api? true
                      attach-www-authenticate-on-missing-bearer-token? true}}
                (setting waiter-url [:authenticator-config :jwt])
                request-headers {"host" waiter-host
                                 "x-forwarded-proto" "https"}
                {:keys [headers] :as response}
                (make-request target-url "/waiter-auth" :disable-auth true :headers request-headers :method :get)
                set-cookie (str (get headers "set-cookie"))
                assertion-message (str (select-keys response [:body :error :headers :status]))]
            (assert-response-status response http-401-unauthorized)
            (is (get headers "www-authenticate") assertion-message)
            (when (and allow-bearer-auth-api? attach-www-authenticate-on-missing-bearer-token?)
              (is (str/includes? (str (get headers "www-authenticate")) "Bearer") assertion-message))
            (is (str/blank? set-cookie) assertion-message))))
      (log/info "JWT authentication is disabled"))))

;; Test disabled because JWT support is, currently, only for tokens
(deftest ^:parallel ^:integration-fast ^:explicit test-unauthorized-jwt-authentication-waiter-realm
  (testing-using-waiter-url
    (if (jwt-auth-enabled? waiter-url)
      (let [waiter-host (-> waiter-url sanitize-waiter-url utils/authority->host)
            access-token (str (retrieve-access-token waiter-host) "invalid")
            request-headers {"authorization" [(str "Bearer " access-token)
                                              ;; absence of Negotiate header also trigger an unauthorized response
                                              (str "SingleUser unauthorized")]
                             "host" waiter-host
                             "x-forwarded-proto" "https"}
            port (waiter-settings-port waiter-url)
            target-url (str waiter-host ":" port)
            {:keys [headers] :as response}
            (make-request target-url "/waiter-auth" :disable-auth true :headers request-headers :method :get)
            set-cookie (str (get headers "set-cookie"))
            assertion-message (str (select-keys response [:body :error :headers :status]))]
        (assert-response-status response http-401-unauthorized)
        (is (str/blank? set-cookie) assertion-message)
        (if-let [challenge (get headers "www-authenticate")]
          (do
            (is (str/includes? (str challenge) "Bearer realm"))
            (is (> (count (str/split challenge #",")) 1) assertion-message))
          (is false (str "www-authenticate header missing: " assertion-message))))
      (log/info "JWT authentication is disabled"))))

(deftest ^:parallel ^:integration-fast test-fallback-to-alternate-auth-on-invalid-jwt-token-waiter-realm
  (testing-using-waiter-url
    (if (jwt-auth-enabled? waiter-url)
      (let [waiter-host (-> waiter-url sanitize-waiter-url utils/authority->host)
            access-token (str (retrieve-access-token waiter-host) "invalid")
            request-headers {"authorization" (str "Bearer " access-token)
                             "host" waiter-host
                             "x-forwarded-proto" "https"}
            port (waiter-settings-port waiter-url)
            target-url (str waiter-host ":" port)
            {:keys [body headers] :as response}
            (make-request target-url "/waiter-auth" :headers request-headers :method :get)
            set-cookie (str (get headers "set-cookie"))
            assertion-message (str {:headers headers
                                    :set-cookie set-cookie
                                    :target-url target-url})]
        (assert-response-status response http-200-ok)
        (is (= (retrieve-username) (str body)))
        (let [{:strs [x-waiter-auth-method]} headers]
          (is (not= "jwt" x-waiter-auth-method) assertion-message)
          (is (not (str/blank? x-waiter-auth-method)) assertion-message))
        (is (= (retrieve-username) (get headers "x-waiter-auth-user")) assertion-message)
        (assert-auth-cookie set-cookie assertion-message))
      (log/info "JWT authentication is disabled"))))

(defn- validate-response
  [service-id access-token auth-method {:keys [body headers] :as response}]
  (let [assertion-message (str {:auth-method auth-method
                                :body body
                                :headers headers
                                :jwt-access-token access-token
                                :service-id service-id})
        set-cookie (str (get headers "set-cookie"))]
    (assert-response-status response http-200-ok)
    (is (= auth-method (get headers "x-waiter-auth-method")) assertion-message)
    (is (= (retrieve-username) (get headers "x-waiter-auth-user")) assertion-message)
    (is (str/blank? set-cookie) assertion-message)
    (let [body-json (try-parse-json body)
          jwt-payload (try-parse-json (get-in body-json ["headers" "x-waiter-jwt-payload"]))]
      (log/info "jwt payload is" jwt-payload)
      (is (= access-token (get-in body-json ["headers" "x-waiter-jwt"])) assertion-message)
      (is (map? jwt-payload) assertion-message)
      (is (every? #(contains? jwt-payload %) ["aud" "exp" "iss" "sub"]) assertion-message))))

(deftest ^:parallel ^:integration-fast test-jwt-authentication-token-realm
  (testing-using-waiter-url
    (if (jwt-auth-enabled? waiter-url)
      (let [waiter-host (-> waiter-url sanitize-waiter-url utils/authority->host)
            host (create-token-name waiter-url ":")
            service-parameters (assoc (kitchen-params)
                                 :env {"USE_BEARER_AUTH" "true"}
                                 :name (rand-name))
            token-response (post-token waiter-url (assoc service-parameters
                                                    :run-as-user (retrieve-username)
                                                    "token" host))
            _ (assert-response-status token-response http-200-ok)
            access-token (retrieve-access-token host)
            request-headers {"authorization" (str "Bearer " access-token)
                             "host" host
                             "x-forwarded-proto" "https"}
            port (waiter-settings-port waiter-url)
            target-url (str waiter-host ":" port)
            {:keys [cookies request-headers service-id] :as response}
            (make-request-with-debug-info
              request-headers
              #(make-request target-url "/request-info" :disable-auth true :headers % :method :get))]
        (try
          (with-service-cleanup
            service-id
            (validate-response service-id access-token "jwt" response)
            (is (empty? cookies) (str response))
            (testing "passing the cookie and bearer token should not use the cookie"
              (let [{:keys [cookies] :as auth-response} (make-request waiter-url "/waiter-auth")]
                (is (seq cookies) (str auth-response))
                (->> (make-request target-url "/request-info"
                                   :cookies cookies
                                   :disable-auth true
                                   :headers (dissoc request-headers "x-cid")
                                   :method :get)
                  (validate-response service-id access-token "jwt")))))
          (finally
            (delete-token-and-assert waiter-url host))))
      (log/info "JWT authentication is disabled"))))

(deftest ^:parallel ^:integration-fast test-fallback-to-alternate-auth-on-invalid-jwt-token-token-realm
  (testing-using-waiter-url
    (if (jwt-auth-enabled? waiter-url)
      (let [waiter-host (-> waiter-url sanitize-waiter-url utils/authority->host)
            host (create-token-name waiter-url ":")
            service-parameters (assoc (kitchen-params)
                                 :env {"USE_BEARER_AUTH" "true"}
                                 :name (rand-name))
            token-response (post-token waiter-url (assoc service-parameters
                                                    :run-as-user (retrieve-username)
                                                    "token" host))
            _ (assert-response-status token-response http-200-ok)
            access-token (str (retrieve-access-token host) "invalid")
            request-headers {"authorization" (str "Bearer " access-token)
                             "host" host
                             "x-forwarded-proto" "https"}
            port (waiter-settings-port waiter-url)
            target-url (str waiter-host ":" port)
            {:keys [headers service-id] :as response}
            (make-request-with-debug-info
              request-headers
              #(make-request target-url "/status" :headers % :method :get))
            set-cookie (str (get headers "set-cookie"))
            assertion-message (str {:headers headers
                                    :service-id service-id
                                    :set-cookie set-cookie
                                    :target-url target-url})]
        (try
          (with-service-cleanup
            service-id
            (assert-response-status response http-200-ok)
            (let [{:strs [x-waiter-auth-method]} headers]
              (is (not= "jwt" x-waiter-auth-method) assertion-message)
              (is (not (str/blank? x-waiter-auth-method)) assertion-message))
            (is (= (retrieve-username) (get headers "x-waiter-auth-user")) assertion-message)
            (assert-auth-cookie set-cookie assertion-message))
          (finally
            (delete-token-and-assert waiter-url host))))
      (log/info "JWT authentication is disabled"))))

(defmacro assert-oidc-challenge-cookie
  "Helper macro to assert the value of x-waiter-oidc-challenge in the set-cookie header."
  [set-cookie assertion-message oidc-same-site-attribute]
  `(let [set-cookie# ~set-cookie
         assertion-message# ~assertion-message
         oidc-same-site-attribute# ~oidc-same-site-attribute]
     (if (str/blank? set-cookie#)
       (is false "set-cookie is blank")
       (do
         (is (str/starts-with? set-cookie# "x-waiter-oidc-challenge-") assertion-message#)
         (is (str/includes? set-cookie# ";Max-Age=") assertion-message#)
         (is (str/includes? set-cookie# ";Path=/") assertion-message#)
         (is (str/includes? set-cookie# ";HttpOnly=true") assertion-message#)
         (when oidc-same-site-attribute#
           (is (str/includes? set-cookie# ";SameSite=None") assertion-message#))
         (when (= "None" oidc-same-site-attribute#)
           (is (str/includes? set-cookie# ";Secure") assertion-message#))))))

(defn- follow-authorize-redirects
  "Asserts for query parameters on the redirect url.
   Then makes requests and follows redirects until the oidc callback url is returned as a redirect."
  [authorize-redirect-location]

  (if (str/blank? authorize-redirect-location)
    (do
      (is false "authorize redirect location is blank")
      nil)
    (do
      (is (str/includes? authorize-redirect-location "client_id="))
      (is (str/includes? authorize-redirect-location "code_challenge="))
      (is (str/includes? authorize-redirect-location "code_challenge_method="))
      (is (str/includes? authorize-redirect-location "redirect_uri="))
      (is (str/includes? authorize-redirect-location "response_type="))
      (is (str/includes? authorize-redirect-location "scope="))
      (is (str/includes? authorize-redirect-location "state="))

      (loop [iteration 1
             authorize-location authorize-redirect-location]
        (log/info "redirecting to" authorize-location)
        (let [authorize-uri (URI. authorize-location)
              authorize-protocol (.getScheme authorize-uri)
              authorize-authority (.getAuthority authorize-uri)
              authorize-path (str (.getPath authorize-uri) "?" (.getQuery authorize-uri))
              authorize-response (make-request authorize-authority authorize-path :protocol authorize-protocol)
              _ (assert-response-status authorize-response #{http-301-moved-permanently
                                                             http-302-moved-temporarily
                                                             http-307-temporary-redirect
                                                             http-308-permanent-redirect})
              assertion-message (str {:headers (:headers authorize-response)
                                      :status (:status authorize-response)
                                      :uri authorize-location})
              {:strs [location]} (:headers authorize-response)]
          (is (not (str/blank? location)) assertion-message)
          (if (or (>= iteration 10)
                  (and (str/includes? location "/oidc/v1/callback?")
                       (str/includes? location "code=")
                       (str/includes? location "state=")))
            location
            (recur (inc iteration) location)))))))

(defn- retrieve-oidc-same-site-attribute
  [waiter-url]
  (or (setting waiter-url [:authenticator-config :jwt :oidc-same-site-attribute]) "None"))

(deftest ^:parallel ^:integration-fast test-oidc-authentication-redirect
  (testing-using-waiter-url
    (if (oidc-auth-enabled? waiter-url)
      (doseq [oidc-auth-env ["true" "relaxed"]] ;; TODO handle enabling test for strict
        (testing (str "OIDC auth with env " oidc-auth-env)
          (let [waiter-host (-> waiter-url sanitize-waiter-url utils/authority->host)
                oidc-token-from-env (System/getenv "WAITER_TEST_TOKEN_OIDC")
                edit-oidc-token-from-env? (Boolean/valueOf (System/getenv "WAITER_TEST_TOKEN_OIDC_EDIT"))
                waiter-token (or oidc-token-from-env (create-token-name waiter-url ":"))
                edit-token? (or (str/blank? oidc-token-from-env) edit-oidc-token-from-env?)
                _ (when edit-token?
                    (let [service-parameters (assoc (kitchen-params)
                                               :env {"USE_OIDC_AUTH" oidc-auth-env}
                                               :name (rand-name)
                                               :run-as-user (retrieve-username))
                          token-response (post-token waiter-url (assoc service-parameters :token waiter-token))]
                      (assert-response-status token-response http-200-ok)))
                ;; absence of Negotiate header also triggers an unauthorized response
                request-headers {"authorization" "SingleUser unauthorized"
                                 "accept-redirect" "yes"
                                 "host" waiter-token
                                 "x-forwarded-proto" "https"}
                port (waiter-settings-port waiter-url)
                target-url (str waiter-host ":" port)
                {:keys [cookies] :as initial-response}
                (make-request-with-debug-info
                  request-headers
                  #(make-request target-url "/request-info" :disable-auth true :headers % :method :get))]
            (try
              (assert-response-status initial-response http-302-moved-temporarily)
              (let [{:strs [location set-cookie]} (:headers initial-response)
                    assertion-message (str {:headers (:headers initial-response)
                                            :oidc-auth-env oidc-auth-env
                                            :set-cookie set-cookie
                                            :status (:status initial-response)})
                    oidc-same-site-attribute (retrieve-oidc-same-site-attribute waiter-url)]
                (is (not (str/blank? location)) assertion-message)
                (assert-oidc-challenge-cookie set-cookie assertion-message oidc-same-site-attribute)

                (when-let [callback-location (follow-authorize-redirects location)]
                  (is (not (str/blank? callback-location)) assertion-message)
                  (is (str/includes? callback-location "/oidc/v1/callback?") assertion-message)
                  (let [callback-uri (URI. callback-location)
                        callback-path (str (.getPath callback-uri) "?" (.getRawQuery callback-uri))
                        callback-request-headers {"host" waiter-token
                                                  "x-forwarded-proto" "https"}
                        {:keys [cookies] :as callback-response}
                        (make-request target-url callback-path
                                      :cookies cookies
                                      :headers callback-request-headers)]
                    (assert-response-status callback-response http-302-moved-temporarily)
                    (is (= 3 (count cookies)) (str cookies))
                    (if-let [oidc-challenge-cookie (first (filter #(str/starts-with? (:name %) "x-waiter-oidc-challenge-") cookies))]
                      (is (= {:http-only? true :max-age 0 :path "/" :secure? true}
                             (select-keys oidc-challenge-cookie [:http-only? :max-age :path :secure?])))
                      (is false "OIDC challenge cookie is missing"))
                    (assert-waiter-authentication-cookies cookies true)
                    (if-let [waiter-auth-cookie (first (filter #(= (:name %) "x-waiter-auth") cookies))]
                      (let [x-waiter-auth-max-age (:max-age waiter-auth-cookie)
                            one-day-in-secs (-> 1 t/days t/in-seconds)]
                        (if (= "strict" oidc-auth-env)
                          (is (< x-waiter-auth-max-age one-day-in-secs) assertion-message)
                          (is (= x-waiter-auth-max-age one-day-in-secs) assertion-message)))
                      (is false "x-waiter-auth cookie is missing"))
                    (let [{:strs [location]} (:headers callback-response)
                          assertion-message (str {:headers (:headers callback-response)
                                                  :status (:status callback-response)})]
                      (is (= (str "https://" waiter-token "/request-info") location) assertion-message))

                    (testing "keep-alive support"
                      (let [request-cookies cookies
                            {:keys [cookies headers] :as response}
                            (make-request waiter-url "/.well-known/auth/keep-alive"
                                          :cookies request-cookies
                                          :disable-auth true
                                          :headers {"accept-redirect" "yes" ;; allow OIDC auth to trigger redirects when required
                                                    "host" waiter-token
                                                    "x-waiter-debug" true
                                                    "x-waiter-single-user" "unauthorized"}
                                          :method :get
                                          :query-params {"offset" "100000000"})
                            {:strs [location]} headers
                            response-auth-expires-at-cookie (extract-cookie cookies "x-auth-expires-at")
                            response-waiter-auth-cookie (extract-cookie cookies "x-waiter-auth")]
                        (assert-waiter-response response)
                        (is (not (str/blank? location)) (str response))
                        (when location
                          (let [location-uri (URI. location)]
                            (is (= "https" (.getScheme location-uri)) (str response))
                            (is (= "/.well-known/auth/keep-alive" (.getPath location-uri)) (str response))
                            (is (-> location-uri (.getRawQuery) (str) (str/includes? "done") not) (str response))))
                        (is (nil? response-auth-expires-at-cookie))
                        (is (nil? response-waiter-auth-cookie)))))))
              (finally
                (when edit-token?
                  (delete-token-and-assert waiter-url waiter-token)))))))
      (log/info "OIDC+PKCE authentication is disabled"))))

(deftest ^:parallel ^:integration-fast test-oidc-authentication-unique-challenge-cookies
  (testing-using-waiter-url
    (if (oidc-auth-enabled? waiter-url)
      (let [waiter-host (-> waiter-url sanitize-waiter-url utils/authority->host)
            oidc-token-from-env (System/getenv "WAITER_TEST_TOKEN_OIDC")
            edit-oidc-token-from-env? (Boolean/valueOf (System/getenv "WAITER_TEST_TOKEN_OIDC_EDIT"))
            waiter-token (or oidc-token-from-env (create-token-name waiter-url ":"))
            edit-token? (or (str/blank? oidc-token-from-env) edit-oidc-token-from-env?)
            _ (when edit-token?
                (let [service-parameters (assoc (kitchen-params)
                                           :env {"USE_OIDC_AUTH" "true"}
                                           :name (rand-name)
                                           :run-as-user (retrieve-username))
                      token-response (post-token waiter-url (assoc service-parameters :token waiter-token))]
                  (assert-response-status token-response http-200-ok)))
            ;; absence of Negotiate header also triggers an unauthorized response
            request-headers {"authorization" "SingleUser unauthorized"
                             "accept-redirect" "yes"
                             "host" waiter-token
                             "x-forwarded-proto" "https"}
            port (waiter-settings-port waiter-url)
            target-url (str waiter-host ":" port)
            response-atom (atom [])
            challenge-cookies-atom (atom #{})
            num-threads 5
            num-iterations 2]
        (try
          (parallelize-requests
            num-threads num-iterations
            (fn test-oidc-authentication-challenge-cookies-task []
              (let [initial-response
                    (make-request-with-debug-info
                      request-headers
                      #(make-request target-url (str "/request-" (rand-int 1000))
                                     :disable-auth true :headers % :method :get))]
                (swap! response-atom conj initial-response))))
          (doseq [initial-response @response-atom]
            (assert-response-status initial-response http-302-moved-temporarily)
            (let [{:strs [location set-cookie]} (:headers initial-response)
                  assertion-message (str {:headers (:headers initial-response)
                                          :set-cookie set-cookie
                                          :status (:status initial-response)})
                  oidc-same-site-attribute (retrieve-oidc-same-site-attribute waiter-url)]
              (is (not (str/blank? location)) assertion-message)
              (assert-oidc-challenge-cookie set-cookie assertion-message oidc-same-site-attribute)
              (swap! challenge-cookies-atom conj (str (first (str/split set-cookie #"=" 2))))))
          (is (= (* num-threads num-iterations) (count @challenge-cookies-atom)) (str @challenge-cookies-atom))
          (finally
            (when edit-token?
              (delete-token-and-assert waiter-url waiter-token)))))
      (log/info "OIDC+PKCE authentication is disabled"))))

(deftest ^:parallel ^:integration-fast test-oidc-authentication-too-many-challenge-cookies
  (testing-using-waiter-url
    (if (oidc-auth-enabled? waiter-url)
      (let [waiter-host (-> waiter-url sanitize-waiter-url utils/authority->host)
            oidc-token-from-env (System/getenv "WAITER_TEST_TOKEN_OIDC")
            edit-oidc-token-from-env? (Boolean/valueOf (System/getenv "WAITER_TEST_TOKEN_OIDC_EDIT"))
            waiter-token (or oidc-token-from-env (create-token-name waiter-url ":"))
            edit-token? (or (str/blank? oidc-token-from-env) edit-oidc-token-from-env?)
            _ (when edit-token?
                (let [service-parameters (assoc (kitchen-params)
                                           :env {"USE_OIDC_AUTH" "true"}
                                           :name (rand-name)
                                           :run-as-user (retrieve-username))
                      token-response (post-token waiter-url (assoc service-parameters :token waiter-token))]
                  (assert-response-status token-response http-200-ok)))
            settings (waiter-settings waiter-url)
            oidc-num-challenge-cookies-allowed-in-request (get-in settings [:authenticator-config :jwt :oidc-num-challenge-cookies-allowed-in-request] 100)
            ;; absence of Negotiate header also triggers an unauthorized response
            request-headers {"authorization" "SingleUser unauthorized"
                             "accept-redirect" "yes"
                             "cookie" (->> (range oidc-num-challenge-cookies-allowed-in-request)
                                        (map #(str "x-waiter-oidc-challenge-" % "=v" %))
                                        (str/join "; "))
                             "host" waiter-token
                             "x-forwarded-proto" "https"}
            port (waiter-settings-port waiter-url)
            target-url (str waiter-host ":" port)]
        (try
          (let [initial-response
                (make-request-with-debug-info
                  request-headers
                  #(make-request target-url (str "/request-" (rand-int 1000))
                                 :disable-auth true :headers % :method :get))]
            (assert-waiter-response initial-response)
            (assert-response-status initial-response http-401-unauthorized))
          (testing "oidc enabled endpoint"
            (let [response (make-request-with-debug-info
                             {:x-waiter-token waiter-token}
                             #(make-kitchen-request waiter-url % :path "/.well-known/oidc/v1/openid-enabled"))]
              (is (= {:client-id waiter-token :enabled true :token? true}
                     (some-> response :body try-parse-json walk/keywordize-keys)))
              (assert-response-status response http-200-ok)
              (assert-waiter-response response))
            (let [{:keys [allow-oidc-auth-api?]} (get-in settings [:authenticator-config :jwt])
                  {:keys [hostname]} settings
                  request-host (if (string? hostname) hostname (first hostname))
                  request-headers {"host" request-host}
                  response (make-kitchen-request waiter-url request-headers :path "/.well-known/oidc/v1/openid-enabled")]
              (is (= {:client-id request-host :enabled (true? allow-oidc-auth-api?) :token? false}
                     (some-> response :body try-parse-json walk/keywordize-keys))
                  (str {:allow-oidc-auth-api? allow-oidc-auth-api?
                        :waiter-host (:host settings)
                        :waiter-hostname (:hostname settings)
                        :waiter-url waiter-url}))
              (assert-response-status response (if allow-oidc-auth-api? http-200-ok http-404-not-found))
              (assert-waiter-response response)))
          (finally
            (when edit-token?
              (delete-token-and-assert waiter-url waiter-token)))))
      (log/info "OIDC+PKCE authentication is disabled"))))

(deftest ^:parallel ^:integration-fast test-spnego-authentication-disabled
  (if use-spnego
    (testing-using-waiter-url
      (let [{:keys [cookies] :as auth-response} (make-request waiter-url "/waiter-auth")
            token-name (create-token-name waiter-url ":")]
        (is (seq cookies) (str auth-response))
        (try
          (let [service-parameters (assoc (kitchen-params)
                                     :authentication "standard"
                                     :env {"USE_BEARER_AUTH" "true"
                                           "USE_SPNEGO_AUTH" "false"}
                                     :name (rand-name)
                                     :run-as-user (retrieve-username))
                token-response (post-token waiter-url (assoc service-parameters "token" token-name))
                _ (assert-response-status token-response http-200-ok)
                {:keys [service-id] :as canary-response}
                (make-request-with-debug-info
                  {:x-waiter-token token-name}
                  #(make-kitchen-request waiter-url % :cookies cookies :path "/request-info"))]
            (with-service-cleanup
              service-id
              (assert-response-status canary-response http-200-ok)
              (assert-backend-response canary-response)
              (is (= "cookie" (get-in canary-response [:headers "x-waiter-auth-method"])) (str canary-response))
              (is (= (retrieve-username) (get-in canary-response [:headers "x-waiter-auth-user"])) (str canary-response))
              (let [response (make-request-with-debug-info
                               {:x-waiter-token token-name}
                               #(make-kitchen-request waiter-url % :path "/request-info"))]
                (assert-response-status response http-401-unauthorized)
                (assert-waiter-response response)
                (let [www-authenticate-header (get-in response [:headers "www-authenticate"])]
                  (is www-authenticate-header (str response))
                  (is (not (str/includes? (str www-authenticate-header) "Negotiate")) (str response))
                  (is (str/includes? (str www-authenticate-header) "Bearer") (str response))))))
          (finally
            (delete-token-and-assert waiter-url token-name)))))
    (log/info "Skipping test as spnego authentication is not available")))
