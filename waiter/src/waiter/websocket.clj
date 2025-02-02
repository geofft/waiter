;;
;; Copyright (c) Two Sigma Open Source, LLC
;;
;; Licensed under the Apache License, Version 2.0 (the "License");
;; you may not use this file except in compliance with the License.
;; You may obtain a copy of the License at
;;
;;  http://www.apache.org/licenses/LICENSE-2.0
;;
;; Unless required by applicable law or agreed to in writing, software
;; distributed under the License is distributed on an "AS IS" BASIS,
;; WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
;; See the License for the specific language governing permissions and
;; limitations under the License.
;;
(ns waiter.websocket
  (:require [clj-time.coerce :as tc]
            [clj-time.core :as t]
            [clojure.core.async :as async]
            [clojure.data.codec.base64 :as b64]
            [clojure.string :as str]
            [clojure.tools.logging :as log]
            [metrics.counters :as counters]
            [metrics.histograms :as histograms]
            [metrics.meters :as meters]
            [metrics.timers :as timers]
            [plumbing.core :as pc]
            [qbits.jet.client.websocket :as ws-client]
            [waiter.auth.authentication :as auth]
            [waiter.cookie-support :as cookie-support]
            [waiter.correlation-id :as cid]
            [waiter.headers :as headers]
            [waiter.metrics :as metrics]
            [waiter.middleware :as middleware]
            [waiter.request-log :as rlog]
            [waiter.scheduler :as scheduler]
            [waiter.statsd :as statsd]
            [waiter.status-codes :refer :all]
            [waiter.util.async-utils :as au]
            [waiter.util.http-utils :as hu]
            [waiter.util.ring-utils :as ru]
            [waiter.util.utils :as utils])
  (:import (java.net HttpCookie SocketTimeoutException URLDecoder URLEncoder)
           (java.nio ByteBuffer)
           (org.eclipse.jetty.websocket.api MessageTooLargeException StatusCode UpgradeRequest)
           (org.eclipse.jetty.websocket.common WebSocketSession)
           (org.eclipse.jetty.websocket.servlet ServletUpgradeRequest ServletUpgradeResponse)))

;; https://tools.ietf.org/html/rfc6455#section-7.4
(def ^:const server-termination-on-unexpected-condition websocket-1011-server-error)

(def ^:const attr-auth-method "ws/auth-method")
(def ^:const attr-auth-principal "ws/auth-principal")
(def ^:const attr-waiter-token "ws/waiter-token")

(defn successful-upgrade?
  "Returns true if the status is 101 indicating the upgrade request was successful."
  [status]
  (and (integer? status)
       (= http-101-switching-protocols status)))

(defn get-upgrade-request-attribute
  "Returns an Object containing the value of the attribute, or nil if the attribute does not exist."
  [^UpgradeRequest upgrade-request attribute-name]
  (when (instance? ServletUpgradeRequest upgrade-request)
    (.getServletAttribute ^ServletUpgradeRequest upgrade-request attribute-name)))

(defn set-upgrade-request-attribute!
  "Stores an attribute in this request."
  [^UpgradeRequest upgrade-request attribute-name attribute-value]
  (when (instance? ServletUpgradeRequest upgrade-request)
    (.setServletAttribute ^ServletUpgradeRequest upgrade-request attribute-name attribute-value)))

(defn log-websocket-upgrade!
  "Publishes the result of an upgrade request to the request log."
  [{:keys [upgrade-request upgrade-response] :as request} response-status]
  (let [response-headers (->> (.getHeaders upgrade-response)
                          (pc/map-vals #(str/join "," %))
                          (pc/map-keys str/lower-case))
        auth-method (get-upgrade-request-attribute upgrade-request attr-auth-method)
        auth-principal (get-upgrade-request-attribute upgrade-request attr-auth-principal)
        waiter-token (get-upgrade-request-attribute upgrade-request attr-waiter-token)
        response (cond->
                   {:headers response-headers
                    :request-type "websocket-upgrade"
                    :waiter-api-call? false}
                   ;; do not output status for 101 as further processing in Jetty may still fail the request
                   (not (successful-upgrade? response-status)) (assoc :status response-status)
                   auth-method (assoc :authorization/method auth-method)
                   auth-principal (assoc :authorization/principal auth-principal)
                   waiter-token (assoc :waiter/token waiter-token))]
    (rlog/log-request! request response)))

(defn make-websocket-request-acceptor
  "Takes a handler and returns a websocket-request-acceptor handler function that takes a special request and response
  object provided on websocket upgrade. It creates a generic request map from the two objects and passes it to the handler"
  [server-name handler]
  (fn websocket-request-acceptor [^ServletUpgradeRequest request ^ServletUpgradeResponse response]
    (let [request-headers (->> (.getHeaders request)
                               (pc/map-vals #(str/join "," %))
                               (pc/map-keys str/lower-case))
          request-id (str "ws-" (utils/unique-identifier))
          correlation-id (or (get request-headers "x-cid") request-id)
          method (some-> request .getMethod str/lower-case keyword)
          scheme (some-> request .getRequestURI .getScheme keyword)
          uri (some-> request .getRequestURI .getPath)]
      (cid/with-correlation-id
        correlation-id
        (log/info "request received (websocket upgrade)"
                  {:headers (headers/truncate-header-values request-headers)
                   :http-version (.getHttpVersion request)
                   :method (some-> request .getMethod str/lower-case)
                   :protocol-version (.getProtocolVersion request)
                   :sub-protocols (some-> request .getSubProtocols seq)
                   :uri uri})
        (.setHeader response "server" server-name)
        (.setHeader response "x-cid" correlation-id)
        (let [handler-request {:client-protocol (some->> request .getProtocolVersion (str "WS/"))
                               :headers (assoc request-headers "x-cid" correlation-id)
                               :internal-protocol (some-> request .getHttpVersion)
                               :query-string (some-> request .getQueryString)
                               :remote-addr (some-> request .getRemoteAddress)
                               :request-id request-id
                               :request-method method
                               :request-time (t/now)
                               :scheme scheme
                               :server-port (some-> request .getLocalPort)
                               :upgrade-request request
                               :upgrade-response response
                               :uri uri}
              response-status (handler handler-request)]
          (log-websocket-upgrade! handler-request response-status)
          (successful-upgrade? response-status))))))

(defn wrap-service-discovery-data
  "Middleware that stores service discovery data inside the ServletUpgradeRequest before passing the request map to the next handler."
  [handler]
  (fn wrap-service-discovery-data-handler
    [{:keys [upgrade-request] :as request}]
    (when-let [token (get-in request [:waiter-discovery :token])]
      (set-upgrade-request-attribute! upgrade-request attr-waiter-token token))
    (handler request)))

(defn request-authenticator
  "Authenticates the request using the x-waiter-auth cookie.
   If authentication fails, a 403 Unauthorized response is sent and false returned to avoid websocket creation in jet.
   If authentication succeeds, true is returned and the websocket handler is eventually invoked."
  [password ^UpgradeRequest request ^ServletUpgradeResponse response]
  (try
    (let [auth-cookie (some (fn auth-filter [^HttpCookie cookie]
                              (when (= auth/AUTH-COOKIE-NAME (.getName cookie))
                                (.getValue cookie)))
                            (seq (.getCookies request)))
          decoded-auth-cookie (and auth-cookie
                                   (-> auth-cookie
                                     (URLDecoder/decode "UTF-8")
                                     (auth/decode-auth-cookie password)))
          auth-cookie-valid? (auth/decoded-auth-valid? decoded-auth-cookie)]
      (if auth-cookie-valid?
        (let [[auth-principal _ _] decoded-auth-cookie]
          (set-upgrade-request-attribute! request attr-auth-method "cookie")
          (set-upgrade-request-attribute! request attr-auth-principal auth-principal)
          http-101-switching-protocols)
        (do
          (log/info "failed to authenticate" {:auth-cookie auth-cookie})
          (.sendForbidden response "Unauthorized")
          http-403-forbidden)))
    (catch Throwable e
      (log/error e "error while authenticating websocket request")
      (.sendError response http-500-internal-server-error (.getMessage e))
      http-500-internal-server-error)))

(defn request-subprotocol-acceptor
  "Associates a subprotocol (when present) in the request with the response.
   Fails the upgrade connection if multiple subprotocols are provided as we are determining the
   subprotocol without talking to the backend."
  [^UpgradeRequest request ^ServletUpgradeResponse response]
  (try
    (let [sec-websocket-protocols (vec (.getHeaders request "sec-websocket-protocol"))]
      (condp = (count sec-websocket-protocols)
        0 (do
            (log/info "no subprotocols provided, accepting upgrade request")
            http-101-switching-protocols)
        1 (let [accepted-subprotocol (first sec-websocket-protocols)]
            (log/info "accepting websocket subprotocol" accepted-subprotocol)
            (.setAcceptedSubProtocol response accepted-subprotocol)
            http-101-switching-protocols)
        (do
          (log/info "rejecting websocket due to presence of multiple subprotocols" sec-websocket-protocols)
          (.sendError response http-500-internal-server-error (str "waiter does not yet support multiple subprotocols in websocket requests: " sec-websocket-protocols))
          http-500-internal-server-error)))
    (catch Throwable th
      (log/error th "error while selecting subprotocol for websocket request")
      (.sendError response http-500-internal-server-error (.getMessage th))
      http-500-internal-server-error)))

(defn inter-router-request-middleware
  "Attaches a dummy x-waiter-auth cookie into the request to enable mimic-ing auth in inter-router websocket requests."
  [router-id password ^UpgradeRequest request]
  (let [router-principal (str router-id "@waiter-peer-router")
        creation-time-millis (tc/to-long (t/now))
        age-in-seconds (-> 1 t/days t/in-seconds)
        cookie-value (auth/create-auth-cookie-value router-principal creation-time-millis age-in-seconds nil)
        auth-cookie-value (URLEncoder/encode ^String (cookie-support/encode-cookie cookie-value password) "UTF-8")]
    (log/info "attaching" auth-cookie-value "to websocket request")
    (-> request
        (.getCookies)
        (.add (HttpCookie. auth/AUTH-COOKIE-NAME auth-cookie-value)))))

(defn request-handler
  "Handler for websocket requests.
   When auth cookie is available, the user credentials are populated into the request.
   It then goes ahead and invokes the process-request-fn handler."
  [password process-request-fn {:keys [headers] :as request}]
  ;; auth-cookie is assumed to be valid when it is present
  (if-let [auth-cookie (-> headers (get "cookie") str auth/get-auth-cookie-value)]
    (let [[auth-principal auth-time auth-metadata] (auth/decode-auth-cookie auth-cookie password)
          auth-params-map (auth/build-auth-params-map :cookie auth-principal auth-metadata)
          handler (middleware/wrap-merge process-request-fn auth-params-map)
          request' (assoc request :waiter/auth-expiry-time auth-time)]
      (log/info "processing websocket request" {:user auth-principal})
      (handler request'))
    (process-request-fn request)))

(defn make-request-handler
  "Returns the handler for websocket requests."
  [password process-request-fn]
  (fn websocket-request-handler [request]
    (request-handler password process-request-fn request)))

(defn abort-request-callback-factory
  "Creates a callback to abort the http request."
  [response]
  (fn abort-websocket-request-callback [^Exception e]
    (log/error e "aborting backend websocket request")
    (let [backend-in (-> response :request :in)
          backend-out (-> response :request :out)]
      (async/close! backend-in)
      (async/close! backend-out))))

(defn add-headers-to-upgrade-request!
  "Sets the headers an on UpgradeRequest."
  [^UpgradeRequest upgrade-request headers]
  (doseq [[key value] headers]
    (let [header-name (if (keyword? key) (name key) (str key))]
      (.setHeader upgrade-request header-name (str value)))))

(defn- dissoc-forbidden-headers
  "Remove websocket forbidden headers based on
   http://archive.eclipse.org/jetty/9.1.0.M0/xref/org/eclipse/jetty/websocket/client/ClientUpgradeRequest.html#52"
  [headers]
  (dissoc headers "cache-control" "cookie" "connection" "host" "pragma" "sec-websocket-accept" "sec-websocket-extensions"
          "sec-websocket-key" "sec-websocket-protocol" "sec-websocket-version" "upgrade"))

(defn make-request
  "Makes an asynchronous websocket request to the instance endpoint and returns a channel."
  [websocket-client service-id->password-fn {:keys [host port] :as instance} {:keys [query-string] :as ws-request}
   request-properties passthrough-headers end-route _ request-proto proto-version]
  (let [ws-middleware (fn ws-middleware [_ ^UpgradeRequest request]
                        (let [service-password (-> instance scheduler/instance->service-id service-id->password-fn)
                              {:keys [authorization/metadata authorization/principal]} ws-request
                              headers
                              (-> (dissoc passthrough-headers "content-length" "expect" "authorization")
                                  (headers/dissoc-hop-by-hop-headers proto-version)
                                  (dissoc-forbidden-headers)
                                  (assoc "Authorization" (str "Basic " (String. ^bytes (b64/encode (.getBytes (str "waiter:" service-password) "utf-8")) "utf-8")))
                                  (headers/assoc-auth-headers principal metadata)
                                  (assoc "x-cid" (cid/get-correlation-id)))]
                          (add-headers-to-upgrade-request! request headers)))
        response (async/promise-chan)
        ctrl-chan (async/chan)
        control-mult (async/mult ctrl-chan)
        sec-websocket-protocol (get-in ws-request [:headers "sec-websocket-protocol"])
        ws-request-properties (cond-> {:async-write-timeout (:async-request-timeout-ms request-properties)
                                       :connect-timeout (:connection-timeout-ms request-properties)
                                       :ctrl (fn ctrl-factory [] ctrl-chan)
                                       :max-idle-timeout (:initial-socket-timeout-ms request-properties)
                                       :middleware ws-middleware}
                                (not (str/blank? sec-websocket-protocol))
                                (assoc :subprotocols (str/split sec-websocket-protocol #",")))
        ws-protocol (if (= "https" (hu/backend-proto->scheme request-proto)) "wss" "ws")
        instance-url (cond-> (scheduler/end-point-url ws-protocol host port end-route)
                       (not (str/blank? query-string))
                       (str "?" query-string))
        service-id (scheduler/instance->service-id instance)
        correlation-id (cid/get-correlation-id)]
    (try
      (log/info "forwarding request for service" service-id "to" instance-url)
      (let [ctrl-copy-chan (async/tap control-mult (async/chan (async/dropping-buffer 1)))]
        (async/go
          (let [[close-code error] (async/<! ctrl-copy-chan)]
            (when (= :qbits.jet.websocket/error close-code)
              ;; the put! is a no-op if the connection was successful
              (log/info "propagating error to response in case websocket connection failed")
              (async/put! response {:error error})))))
      (ws-client/connect! websocket-client instance-url
                          (fn [request]
                            (cid/cinfo correlation-id "successfully connected with backend")
                            (async/put! response {:ctrl-mult control-mult, :request request}))
                          ws-request-properties)
      (let [{:keys [requests-waiting-to-stream]} (metrics/stream-metric-map service-id)]
        (counters/inc! requests-waiting-to-stream))
      (catch Exception exception
        (log/error exception "error while making websocket connection to backend instance")
        (async/put! response {:error exception})))
    response))

(defn- close-requests!
  "Closes all channels associated with a websocket request.
   This includes:
   1. in and out channels from the client,
   2. in and out channels to the backend,
   3. the request-state-chan opened to track the state of the request internally."
  [request response request-state-chan]
  (let [client-out (:out request)
        backend-out (-> response :request :out)]
    (log/info "closing websocket channels")
    (async/close! client-out)
    (async/close! backend-out)
    (async/close! request-state-chan)))

(defn- process-incoming-data
  "Processes the incoming data and return the tuple [bytes-read data-to-send].
   If the incoming data is a ByteBuffer, it is consumed and copied into a newly created byte array (the data-to-send).
   The bytes-read is the size of the byte array in this case.
   In all other scenarios, in-data is converted to a String as data-to-send and the the utf-8 encoding is used for bytes read."
  [in-data]
  (if (instance? ByteBuffer in-data)
    (let [bytes-read (.remaining in-data)
          data-to-send (byte-array bytes-read)]
      (.get in-data data-to-send)
      [bytes-read data-to-send])
    (let [bytes-read (-> in-data (.getBytes "utf-8") count)]
      [bytes-read in-data])))

(defn- stream-helper
  "Helper function to stream data between two channels with support for timeout that recognizes backpressure."
  [src-name src-chan dest-name dest-chan streaming-timeout-ms reservation-status-promise stream-error-type
   request-close-chan stream-onto-upload-chan-timer stream-back-pressure-meter notify-bytes-read-fn]
  (let [upload-chan (async/chan 5)] ;; use same magic 5 as resp-chan in stream-http-response
    (async/pipe upload-chan dest-chan)
    (async/go
      (try
        (loop [bytes-streamed 0]
          (if-let [in-data (async/<! src-chan)]
            (let [[bytes-read send-data] (process-incoming-data in-data)]
              (log/info "received" bytes-read "bytes from" src-name)
              (notify-bytes-read-fn bytes-read)
              (if (timers/start-stop-time!
                    stream-onto-upload-chan-timer
                    (au/timed-offer! upload-chan send-data streaming-timeout-ms))
                (recur (+ bytes-streamed bytes-read))
                (do
                  (log/error "unable to stream to" dest-name {:cid (cid/get-correlation-id), :bytes-streamed bytes-streamed})
                  (meters/mark! stream-back-pressure-meter)
                  (deliver reservation-status-promise stream-error-type)
                  (async/>! request-close-chan stream-error-type))))
            (log/info src-name "input channel has been closed, bytes streamed:" bytes-streamed)))
        (catch Exception e
          (log/error e "error in streaming data from" src-name "to" dest-name)
          (deliver reservation-status-promise :generic-error)
          (async/>! request-close-chan :generic-error))))))

(defn- stream-response
  "Writes byte data to the resp-chan.
   It is assumed the body is an input stream.
   The function buffers bytes, and pushes byte input streams onto the channel until the body input stream is exhausted."
  [request response descriptor {:keys [streaming-timeout-ms]} reservation-status-promise request-close-chan local-usage-agent
   {:keys [requests-streaming requests-waiting-to-stream stream-back-pressure stream-read-body stream-onto-resp-chan] :as metric-map}]
  (let [{:keys [service-description service-id]} descriptor
        {:strs [metric-group]} service-description]
    (counters/dec! requests-waiting-to-stream)
    (counters/inc! requests-streaming)
    ;; launch go-block to stream data from client to instance
    (let [client-in (:in request)
          instance-out (-> response :request :out)
          throughput-meter (metrics/service-meter service-id "streaming" "request-bytes")
          throughput-meter-global (metrics/waiter-meter "streaming" "request-bytes")
          throughput-iterations-meter (metrics/service-meter service-id "streaming" "request-iterations")
          throughput-iterations-meter-global (metrics/waiter-meter "streaming" "request-iterations")]
      (stream-helper "client" client-in "instance" instance-out streaming-timeout-ms reservation-status-promise
                     :instance-error request-close-chan stream-read-body stream-back-pressure
                     (fn ws-bytes-uploaded [bytes-streamed]
                       (meters/mark! throughput-meter bytes-streamed)
                       (meters/mark! throughput-meter-global bytes-streamed)
                       (meters/mark! throughput-iterations-meter)
                       (meters/mark! throughput-iterations-meter-global)
                       (send local-usage-agent metrics/update-last-request-time-usage-metric service-id (t/now))
                       (histograms/update! (metrics/service-histogram service-id "request-size") bytes-streamed)
                       (statsd/inc! metric-group "request_bytes" bytes-streamed))))
    ;; launch go-block to stream data from instance to client
    (let [client-out (:out request)
          instance-in (-> response :request :in)
          {:keys [throughput-iterations-meter throughput-iterations-meter-global throughput-meter throughput-meter-global]} metric-map]
      (stream-helper "instance" instance-in "client" client-out streaming-timeout-ms reservation-status-promise
                     :client-error request-close-chan stream-onto-resp-chan stream-back-pressure
                     (fn ws-bytes-downloaded [bytes-streamed]
                       (meters/mark! throughput-meter bytes-streamed)
                       (meters/mark! throughput-meter-global bytes-streamed)
                       (meters/mark! throughput-iterations-meter)
                       (meters/mark! throughput-iterations-meter-global)
                       (histograms/update! (metrics/service-histogram service-id "response-size") bytes-streamed)
                       (statsd/inc! metric-group "response_bytes" bytes-streamed))))))

(defn watch-ctrl-chan
  "Inspects the return value by tapping on the control-mult and triggers closing of the websocket request."
  [source control-mult reservation-status-promise request-close-promise-chan on-close-callback]
  (let [tapped-ctrl-chan (async/tap control-mult (async/chan (async/dropping-buffer 1)))]
    ;; go-block to trigger close when control-mult has been notified of an event
    (async/go
      (let [[ctrl-code return-code-or-exception close-message] (async/<! tapped-ctrl-chan)]
        (log/info "received on" (name source) "ctrl chan:" ctrl-code
                  (when (integer? return-code-or-exception)
                    ;; Close status codes https://tools.ietf.org/html/rfc6455#section-7.4
                    (case (int return-code-or-exception)
                      websocket-1000-normal "closed normally"
                      websocket-1001-shutdown "shutdown"
                      websocket-1002-protocol "protocol error"
                      websocket-1003-bad-data "unsupported input data"
                      websocket-1006-abnormal "closed abnormally"
                      websocket-1007-bad-payload "unsupported payload"
                      websocket-1008-policy-violation "policy violation"
                      (str "status code " return-code-or-exception))))
        (if (integer? return-code-or-exception)
          (on-close-callback return-code-or-exception)
          (on-close-callback server-termination-on-unexpected-condition))
        (let [close-code (cond
                           (or (nil? ctrl-code)
                               (and (integer? return-code-or-exception)
                                    (StatusCode/isFatal return-code-or-exception)))
                           :connection-closed

                           (= ctrl-code :qbits.jet.websocket/close)
                           :success

                           (= ctrl-code :qbits.jet.websocket/error)
                           (let [error-code (cond
                                              (instance? MessageTooLargeException return-code-or-exception) :generic-error
                                              (instance? SocketTimeoutException return-code-or-exception) :socket-timeout
                                              :else (keyword (str (name source) "-error")))]
                             (deliver reservation-status-promise error-code)
                             (log/error return-code-or-exception "error from" (name source) "websocket request")
                             error-code)

                           :else :unknown)]
          (log/info (name source) "requesting close of websocket:" close-code close-message)
          (async/>! request-close-promise-chan [source close-code return-code-or-exception close-message]))))))

(defn- close-client-session!
  "Explicitly closes the client connection using the provided status and message."
  [request status-code close-message]
  (try
    (let [^WebSocketSession client-session (-> request :ws (.session))]
      (when (some-> client-session .isOpen)
        (log/info "closing client session with code" status-code close-message)
        (.close client-session status-code close-message)))
    (catch Exception e
      (log/error e "error in explicitly closing client websocket using" status-code close-message))))

(defn- successful?
  "Returns whether the status represents a successful status code."
  [status]
  (= websocket-1000-normal status))

(defn process-response!
  "Processes a response resulting from a websocket request.
   It includes asynchronously streaming the content."
  [local-usage-agent instance-request-properties descriptor _ request _ reservation-status-promise
   confirm-live-connection-with-abort request-state-chan response]
  (let [{:keys [service-description service-id]} descriptor
        {:strs [metric-group]} service-description
        request-close-promise-chan (async/promise-chan)
        {:keys [requests-streaming stream stream-complete-rate stream-request-rate] :as metrics-map}
        (metrics/stream-metric-map service-id)]

    ;; go-block that handles cleanup by closing all channels related to the websocket request
    (async/go
      ;; approximate streaming rate by when the connection is closed
      (metrics/with-meter
        stream-request-rate
        stream-complete-rate
        (timers/start-stop-time!
          stream
          (when-let [close-message-wrapper (async/<! request-close-promise-chan)]
            (let [[source close-code status-code-or-exception close-message] close-message-wrapper]
              (log/info "websocket connections requested to be closed due to" source close-code close-message)
              (counters/dec! requests-streaming)
              ;; explicitly close the client connection if backend triggered the close
              (when (= :instance source)
                (let [correlation-id (cid/get-correlation-id)]
                  (async/>!
                    (:out request)
                    (fn close-session [_]
                      (cid/with-correlation-id
                        correlation-id
                        (if (integer? status-code-or-exception)
                          (close-client-session! request status-code-or-exception close-message)
                          (let [ex-message (or (some-> status-code-or-exception .getMessage) close-message)]
                            (close-client-session! request server-termination-on-unexpected-condition ex-message))))))))
              ;; close client and backend channels
              (close-requests! request response request-state-chan))))))

    ;; watch for ctrl-chan events
    (->> (fn client-on-close-callback [status]
           (deliver reservation-status-promise (if (successful? status) :success :client-error)))
         (watch-ctrl-chan :client (-> request :ctrl-mult) reservation-status-promise request-close-promise-chan))
    (->> (fn instance-on-close-callback [status]
           (counters/inc! (metrics/service-counter service-id "response-status" (str status)))
           (statsd/inc! metric-group (str "response_status_" status))
           (deliver reservation-status-promise (if (successful? status) :success :instance-error)))
         (watch-ctrl-chan :instance (:ctrl-mult response) reservation-status-promise request-close-promise-chan))

    (try
      ;; stream data between client and instance
      (stream-response request response descriptor instance-request-properties reservation-status-promise
                       request-close-promise-chan local-usage-agent metrics-map)

      ;; force close connection
      ;; - a day after the auth cookie expires if it is available, or
      ;; - a day after the unauthenticated request is made
      (let [current-time-ms (System/currentTimeMillis)
            expiry-start-time (:waiter/auth-expiry-time request current-time-ms)
            one-day-in-millis (-> 1 t/days t/in-millis)
            expiry-time-ms (+ expiry-start-time one-day-in-millis)
            time-left-ms (max (- expiry-time-ms current-time-ms) 0)]
        (async/go
          (let [timeout-ch (async/timeout time-left-ms)
                [_ selected-chan] (async/alts! [request-close-promise-chan timeout-ch] :priority true)]
            (when (= timeout-ch selected-chan)
              (try
                ;; close connections if the request is still live
                (confirm-live-connection-with-abort)
                (log/info "cookie has expired, triggering closing of websocket connections")
                (async/>! request-close-promise-chan [:cookie-expired nil nil "Cookie Expired"])
                (catch Exception _
                  (log/debug "ignoring exception generated from closed connection")))))))
      (catch Exception e
        (async/>!! request-close-promise-chan [:process-error nil e "Unexpected error"])
        (log/error e "error while processing websocket response"))))
  ;; return an empty response map to maintain consistency with the http case
  {})

(defn wrap-ws-close-on-error
  "Closes the out chan when the handler returns an error."
  [handler]
  (fn wrap-ws-close-on-error-handler [{:keys [out] :as request}]
    (let [response (handler request)]
      (ru/update-response response
                          (fn [response]
                            (when (ru/error-response? response)
                              (async/close! out))
                            response)))))

(defn wrap-ws-acceptor-error-handling
  "wraps a handler and catches any uncaught exceptions and sends an appropriate error response"
  [handler]
  (fn wrap-ws-error-handling-fn [{^ServletUpgradeResponse upgrade-response :upgrade-response :as request}]
    (try
      (handler request)
      (catch Exception e
        (let [{:keys [message status]} (utils/exception->response-metadata e)]
          (.sendError upgrade-response status message))))))
